"""`#200-D`: the operational composition authority for AgentReview v2.

## Why this module exists

Every stage of the v2 pipeline was already implemented and qualified, but
nothing composed them. ``run_synthetic_review_v2`` already owns the entire
back half -- transport, source-specific proof, binding, parsing, synthesis,
readiness decision and ``ReviewReadinessV2`` emission -- and already accepts
either the offline envelope transport or the real Agent Router transport. Its
three inputs, however (``content``, ``manifest``, ``payload_by_chunk_id``),
had no producer: no function in ``app/`` or ``scripts/`` could take a
repository checkout and reach them. That wiring existed only inside tests.

This module is that missing producer, and nothing more:

    repo checkout + profile + base/head
        -> profile / grouping policy
        -> authoritative diff
        -> ManifestV2
        -> ChunkPayloadV2[]
        -> ReviewContentV2
        -> preparation closure
        -> run_synthetic_review_v2   (UNCHANGED)

## What this module deliberately is NOT

It is not a second orchestrator. It re-implements no stage: no diff parser,
no manifest builder, no payload builder, no DLP/redaction, no receipt
consumer, no response binder, no parser, no synthesis and no readiness
authority. Every step below delegates to the module that already owns it, and
converts that module's own ``reason_code`` into a single operational refusal
type without inventing new taxonomy.

It also never issues authority. ``policies`` and the repository identity are
DERIVED from the loaded profile; ``origin``, ``snapshot``, ``toolchain_digest``
and ``pr_state`` are caller-owned facts that must have already crossed their
own canonical parser boundary (`#201-C0`). Nothing here fabricates a snapshot,
an origin, or a digest to make a run succeed.

## Staleness bound (`#200-D`, deliberate)

``run_synthetic_review_v2`` uses ``manifest.identity`` for BOTH ``identity``
and ``evaluated_identity``; it does not independently observe whether the PR
head moved after the run began. This module inherits that bound unchanged and
does not add a live head observer: callers here supply explicit, immutable
``base_sha``/``head_sha``. Independent live-head observation and stale
detection belong to the live-canary grant, not to this composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    FindingLifecycleRecordV2,
    PullRequestStateV2,
    RunOriginV2,
    TargetProfileV2,
)
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.diff_acquisition_v2 import (
    DiffAcquisitionError,
    acquire_authoritative_diff_v2,
)
from app.agent_review.payload_builder_v2 import (
    PayloadBuilderError,
    build_chunk_payloads_from_profile_v2,
)
from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2
from app.agent_review.payload_set_v2 import PayloadSetBindingError
from app.agent_review.profile_loader_v2 import (
    TargetProfileLoadErrorV2,
    load_target_profile_v2,
)
from app.agent_review.review_content_extraction_v2 import (
    ExtractionBlockedError,
    extract_review_content_v2,
)
from app.agent_review.review_content_v2 import (
    CONTENT_CHUNK_SET_MISMATCH_REASON_V2,
    CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2,
    ReviewContentV2,
)
from app.agent_review.review_transport_v2 import (
    ChunkReviewTransportV2,
    SyntheticReviewOutcomeV2,
    run_synthetic_review_v2,
)
from app.agent_review.run_assembly_v2 import (
    RunAssemblyError,
    assemble_manifest_from_diff_v2,
)
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingError,
    SemanticGroupingPolicyV2,
    bind_semantic_grouping_policy_to_target_profile_v2,
)

__all__ = [
    "OperationalReviewOutcomeV2",
    "OperationalRunError",
    "PAYLOAD_SET_INVALID_REASON_V2",
    "RUN_IDENTITY_INVALID_REASON_V2",
    "PreparedReviewRunV2",
    "prepare_operational_review_v2",
    "run_operational_review_v2",
]

# `#200-D` owns no taxonomy of its own beyond this one code, which names a
# condition no upstream authority is positioned to report: the manifest,
# payloads and content each validated against their own contracts, but the
# three do not describe the same chunk set. Every other refusal below reuses
# the exact `reason_code` its owning authority already raised.
PREPARATION_CHUNK_SET_MISMATCH_REASON_V2 = "operational_preparation_chunk_set_mismatch"

# `assemble_manifest_from_diff_v2` constructs `RunIdentityV2` from the caller's
# identity material, so contract-invalid material surfaces as a raw pydantic
# `ValidationError` rather than a `RunAssemblyError`. That must not escape as a
# traceback: it is a caller-input refusal like any other. No upstream authority
# owns a code for it, so this module names it -- namespaced, so it can never be
# mistaken for an upstream authority's own reason.
RUN_IDENTITY_INVALID_REASON_V2 = "operational_run_identity_invalid"

# The emitted payload set failed its own contract for a reason `PayloadSetBindingError`
# does not name (it surfaces as a pydantic `ValidationError`). Namespaced for
# the same reason as the codes above.
PAYLOAD_SET_INVALID_REASON_V2 = "operational_payload_set_invalid"


ASSEMBLY_BLOCKED_REASON_V2 = "assembly_blocked"


class OperationalRunError(ValueError):
    """Fail-closed operational refusal carrying a content-free reason code.

    The reason code is the ORIGINATING authority's own code wherever one
    exists, so a caller can tell profile failure from diff failure from
    extraction failure without this module inventing synonyms.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PreparedReviewRunV2:
    """The front half's product: exactly the three inputs the existing back
    half requires, plus the profile they were all derived from.

    Internal composition value, deliberately not a wire contract: it has no
    schema, no hash and is never persisted.
    """

    profile: TargetProfileV2
    manifest: ManifestV2
    payload_by_chunk_id: Mapping[str, ChunkPayloadV2]
    content: ReviewContentV2


@dataclass(frozen=True)
class OperationalReviewOutcomeV2:
    """One complete operational run. Also internal, also never persisted."""

    prepared: PreparedReviewRunV2
    review: SyntheticReviewOutcomeV2


def prepare_operational_review_v2(
    *,
    repo_root: Path | str,
    target_profile_root: Path | str,
    grouping_policy: SemanticGroupingPolicyV2,
    base_sha: str,
    head_sha: str,
    tested_merge_sha: str,
    pr_number: int,
    toolrepo_sha: str,
    evidence_hash: str,
    max_lines_per_chunk: int,
    dlp_policy=None,
) -> PreparedReviewRunV2:
    """Run the front half against a real checkout, in authority order.

    Each stage's failure prevents every later stage from running -- in
    particular, no payload is built for a blocked assembly and no content is
    extracted for an unbuildable payload set. Because the transport is not
    reachable from this function at all, a front-half failure structurally
    cannot produce a Router call.
    """

    repo_root = Path(repo_root)
    target_profile_root = Path(target_profile_root)

    try:
        profile = load_target_profile_v2(target_profile_root)
    except TargetProfileLoadErrorV2 as exc:
        raise OperationalRunError(exc.reason_code) from exc

    # The policy must be usable against THIS profile, not merely well-formed.
    try:
        bind_semantic_grouping_policy_to_target_profile_v2(grouping_policy, profile)
    except SemanticGroupingError as exc:
        raise OperationalRunError(exc.reason_code) from exc

    try:
        file_diffs = acquire_authoritative_diff_v2(
            repo_root, base_sha=base_sha, head_sha=head_sha
        )
    except DiffAcquisitionError as exc:
        raise OperationalRunError(exc.reason_code) from exc

    try:
        outcome = assemble_manifest_from_diff_v2(
            file_diffs,
            profile=profile,
            grouping_policy=grouping_policy,
            # DERIVED from the profile, never a caller-declared repository
            # identity that could disagree with the profile that governs it.
            repo=profile.identity.repo,
            pr_number=pr_number,
            base_sha=base_sha,
            head_sha=head_sha,
            tested_merge_sha=tested_merge_sha,
            toolrepo_sha=toolrepo_sha,
            evidence_hash=evidence_hash,
            max_lines_per_chunk=max_lines_per_chunk,
        )
    except RunAssemblyError as exc:
        raise OperationalRunError(exc.reason_code) from exc
    except ValidationError as exc:
        # contract-invalid run identity material (bad sha/hash/pr identity)
        raise OperationalRunError(RUN_IDENTITY_INVALID_REASON_V2) from exc

    if outcome.state != "assembled" or outcome.manifest is None:
        # `blocked_reason` is an `AssemblyBlockedReasonV2` DATACLASS carrying
        # `affected_paths` and a human `detail`. Passing it whole would put
        # target file paths into `reason_code` and therefore onto stderr --
        # only its stable code may cross this boundary.
        blocked = outcome.blocked_reason
        raise OperationalRunError(
            blocked.reason_code if blocked is not None else ASSEMBLY_BLOCKED_REASON_V2
        )
    manifest = outcome.manifest

    try:
        built = build_chunk_payloads_from_profile_v2(
            manifest, profile=profile, repo_root=repo_root
        )
    except PayloadBuilderError as exc:
        raise OperationalRunError(exc.reason_code) from exc
    payload_by_chunk_id = {item.chunk_id: item.payload for item in built}

    # Manifest <-> payload closure is an authority that already exists: reuse
    # it rather than re-deriving run_id/manifest_hash/chunk-set agreement.
    #
    # Only when there IS a payload. `PayloadSetV2` contractually requires at
    # least one entry, so a legitimately empty-but-assembled manifest (say,
    # only non-must-review binaries changed) would raise a raw pydantic
    # `ValidationError` here and mask the authority that actually owns
    # "nothing to review": extraction's own typed refusal, below.
    if built:
        try:
            emit_payload_set_v2(manifest, [item.payload for item in built])
        except PayloadSetBindingError as exc:
            raise OperationalRunError(exc.reason_code) from exc
        except ValidationError as exc:
            raise OperationalRunError(PAYLOAD_SET_INVALID_REASON_V2) from exc

    try:
        content = extract_review_content_v2(
            repo_root=repo_root,
            base_sha=base_sha,
            head_sha=head_sha,
            manifest=manifest,
            payload_sha256_by_chunk_id={
                chunk_id: payload.payload_sha256
                for chunk_id, payload in payload_by_chunk_id.items()
            },
            target_profile=profile,
            dlp_policy=dlp_policy,
        )
    except ExtractionBlockedError as exc:
        raise OperationalRunError(exc.reason_code) from exc

    _establish_preparation_closure_v2(
        manifest=manifest, payload_by_chunk_id=payload_by_chunk_id, content=content
    )
    return PreparedReviewRunV2(
        profile=profile,
        manifest=manifest,
        payload_by_chunk_id=payload_by_chunk_id,
        content=content,
    )


def _establish_preparation_closure_v2(
    *,
    manifest: ManifestV2,
    payload_by_chunk_id: Mapping[str, ChunkPayloadV2],
    content: ReviewContentV2,
) -> None:
    """The last gate before the back half.

    ``run_synthetic_review_v2`` indexes ``payload_by_chunk_id[chunk_content.
    chunk_id]`` directly. A raw ``KeyError`` must never be what establishes
    composition correctness, so the three chunk sets are proved equal here --
    as a typed refusal, before any transport exists to be called.

    Content <-> manifest binding already ran inside
    ``extract_review_content_v2``; manifest <-> payload closure already ran
    in ``emit_payload_set_v2``. This adds only the content <-> payload edge
    those two do not span.
    """

    manifest_chunk_ids = {chunk.chunk_id for chunk in manifest.chunks}
    payload_chunk_ids = set(payload_by_chunk_id)
    content_chunk_ids = {chunk.chunk_id for chunk in content.chunks}
    if not (manifest_chunk_ids == payload_chunk_ids == content_chunk_ids):
        raise OperationalRunError(PREPARATION_CHUNK_SET_MISMATCH_REASON_V2)

    for chunk in content.chunks:
        payload = payload_by_chunk_id[chunk.chunk_id]
        if chunk.payload_sha256 != payload.payload_sha256:
            raise OperationalRunError(CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2)
        if chunk.chunk_id != payload.chunk_id:
            raise OperationalRunError(CONTENT_CHUNK_SET_MISMATCH_REASON_V2)


def run_operational_review_v2(
    *,
    repo_root: Path | str,
    target_profile_root: Path | str,
    grouping_policy: SemanticGroupingPolicyV2,
    base_sha: str,
    head_sha: str,
    tested_merge_sha: str,
    pr_number: int,
    toolrepo_sha: str,
    evidence_hash: str,
    transport: ChunkReviewTransportV2,
    pr_state: PullRequestStateV2,
    origin: RunOriginV2,
    snapshot,
    toolchain_digest: str,
    max_lines_per_chunk: int,
    dlp_policy=None,
    checks: Sequence = (),
    provenance: Sequence = (),
    prior_lifecycle: Sequence[FindingLifecycleRecordV2] = (),
) -> OperationalReviewOutcomeV2:
    """Compose the front half onto the existing back half.

    ``transport`` is injected, never constructed here: this module knows
    nothing about HTTP. Network ownership stays in
    ``agent_router_transport_v2`` and offline ownership in
    ``offline_file_transport_v2``, so composition and transport remain
    independently testable.

    ``policies`` and ``target_profile_root`` are derived from the profile the
    front half already loaded, so the run cannot be governed by one profile
    and judged by another. ``checks``/``provenance`` remain CLAIMS: they are
    re-verified by ``produce_review_readiness_v2`` against `#201-C0`'s real
    boundary, and an empty submission degrades honestly to
    ``authority_not_established`` rather than to an approved empty set.
    """

    prepared = prepare_operational_review_v2(
        repo_root=repo_root,
        target_profile_root=target_profile_root,
        grouping_policy=grouping_policy,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=tested_merge_sha,
        pr_number=pr_number,
        toolrepo_sha=toolrepo_sha,
        evidence_hash=evidence_hash,
        max_lines_per_chunk=max_lines_per_chunk,
        dlp_policy=dlp_policy,
    )

    # The back half reaches authorities with their own typed error families
    # (check policy, provenance, readiness emission, content binding). Each
    # already carries a stable `reason_code`; none may escape this
    # composition as a raw traceback -- especially not after the Router call
    # has already been made and paid for.
    try:
        review = run_synthetic_review_v2(
            content=prepared.content,
            manifest=prepared.manifest,
            payload_by_chunk_id=dict(prepared.payload_by_chunk_id),
            transport=transport,
            policies=prepared.profile.policies,
            pr_state=pr_state,
            origin=origin,
            snapshot=snapshot,
            toolchain_digest=toolchain_digest,
            target_profile_root=str(Path(target_profile_root)),
            checks=checks,
            provenance=provenance,
            prior_lifecycle=prior_lifecycle,
        )
    except OperationalRunError:
        raise
    except Exception as exc:
        # Only a failure that already carries a stable `reason_code` is a
        # typed authority refusal. Anything else is a defect in this
        # repository and must stay a crash -- never be laundered into a
        # sanitized review verdict (the same rule PR #270 established for
        # the Router HTTP boundary).
        reason_code = getattr(exc, "reason_code", None)
        if not isinstance(reason_code, str) or not reason_code:
            raise
        raise OperationalRunError(reason_code) from exc
    return OperationalReviewOutcomeV2(prepared=prepared, review=review)
