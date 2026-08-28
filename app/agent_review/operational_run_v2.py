"""`#200-D` successor: the operational composition authority for AgentReview
v2 (issue #200).

## Why this module exists

Every stage of the v2 pipeline is already implemented and qualified, but
nothing composes them. ``run_synthetic_review_v2`` already owns the entire
back half -- transport, source-specific proof, binding, parsing, synthesis,
readiness decision and ``ReviewReadinessV2`` emission -- and already accepts
either the offline envelope transport or the real Agent Router transport.
Its three inputs (``content``, ``manifest``, ``payload_by_chunk_id``), had no
producer on live master: no function in ``app/`` or ``scripts/`` could take a
repository checkout and reach them.

## Forensic predecessor and integrated predecessor

PR #271 built this composer and was reviewed six times, each round finding
the same class of defect: a stage's ``except`` list was narrower than the
exception surface beneath it. It returned ``STOP_ARCHITECTURAL_BOUNDARY`` --
diagnosing that each authority's surface was OPEN, so no amount of consumer
inspection could enumerate it. PR #272 (merged) closed those surfaces AT
THEIR OWNERS under a two-epoch model:

    caller / external / environment material
      -> owner validation, parsing, acquisition classification
      -> SEAL
      -> internal derivation

Only pre-seal failures convert to a typed refusal; a post-seal
``ValidationError``/``TypeError``/etc. is a repository defect and escapes
raw. This module is the successor built ON TOP of that closure -- it needs
no compensating knowledge of any authority's internals, only that each one
refused. PR #271's runner is NOT ported and NOT modified; #272 made most of
its compensating machinery obsolete (``run_assembly_identity_invalid``,
``payload_set_empty``, ``repo_root_unusable``, ``git_unavailable``,
``payload_contract_unreadable`` and the ``max_lines_per_chunk`` type/value
guard are now each owned at their source, not pre-validated here).

## What this module deliberately is NOT

It is not a second orchestrator. It re-implements no stage: no diff parser,
no manifest builder, no payload builder, no DLP/redaction, no receipt
consumer, no response binder, no parser, no synthesis and no readiness
authority. It also never issues authority: ``policies`` and the repository
identity are DERIVED from the loaded profile; ``origin``, ``snapshot``,
``toolchain_digest`` and ``pr_state`` are caller-owned facts that must have
already crossed their own canonical parser boundary (`#201-C0`).

## Two new authorities this successor adds

``toolrepo_identity_v2.establish_toolrepo_source_identity_v2`` proves the
EXECUTING toolrepo's own source checkout matches its declared
``toolrepo_sha`` before any semantic review runs -- the caller's declaration
is never treated as proof of what actually executed.

``reference_source_v2.resolve_reference_source_v2`` materializes every
profile-declared artifact/contract reference that exists as a regular Git
blob at ``head_sha`` into a private directory, and the (UNCHANGED) payload
owner is pointed at that directory instead of the target's mutable working
tree -- closing a TOCTOU an earlier design (preflight-check-then-reread) did
not actually close. See that module's docstring for the full property.

## Error surface -- measured against the CURRENT call graph, not inherited

Each stage below catches EXACTLY the public family its owner documents for
itself on current master; nothing here catches ``pydantic.ValidationError``,
a raw ``OSError``, ``PayloadReferenceError`` (a SIBLING family the payload
owner already converts -- see ``payload_builder_v2.build_chunk_payloads_
from_profile_v2``'s own docstring), ``except Exception``/``BaseException``,
or inspects a dynamic ``reason_code`` off an untyped exception. If an
expected condition is ever found escaping through one of those raw families,
that is `STOP_OWNER_SURFACE_REOPENED` -- to be fixed at the owner, never
compensated for here.

The back half (``run_synthetic_review_v2``) is wrapped once, catching every
family PROVEN reachable by reading its current call graph rather than by
copying #271's historical clause list (that list was already stale on
current master: ``synthesis_v2.py`` converts ``ChunkResultScopeError`` into
``SynthesisErrorV2`` before it ever reaches this composer, so preserving a
catch for the former would be dead code masquerading as coverage):

    SynthesisErrorV2                 chunk-result scope violation (converted)
    LifecycleAggregationError        prior_lifecycle revalidation refusal --
                                      reached DIRECTLY: synthesis calls the
                                      PRIVATE `_aggregate_finding_lifecycle_
                                      core_v2` and does not convert it
    ReadinessDecisionError           C1 decision refusal (converts its own
                                      FragmentCoverageBindingError internally)
    TargetProfileLoadErrorV2         `produce_review_readiness_v2` re-loads
                                      `target_profile_root` a SECOND time,
                                      independently of this module's own
                                      front-half load, to derive the required-
                                      check set (`#201-C0`) -- the identical
                                      family this module already catches once
                                      is reachable a second time through a
                                      different call path, and closing one
                                      entry point is not closing the
                                      authority (#272's own round-3 lesson)
    AuthoritativeCheckPolicyErrorV2  the base-checkout policy load/cross-
                                      validate the same C0 frontier performs
    RequiredCheckReadinessErrorV2    required-check completeness assessment
    RequiredCheckProvenanceErrorV2   the C0 frontier itself -- its own module
                                      docstring states it is never caught
                                      "anywhere in between"
    ReadinessEmissionError           pre-seal `ready`-precondition refusal

``parse_bound_chunk_response_v2`` (inside ``execute_chunk_review_v2``, itself
inside the comprehension in ``run_synthetic_review_v2``) raises a raw
``TypeError`` for anything not produced by the binder -- source proof and
binding have already passed by that point, so this is a PROGRAMMER DEFECT
and must never become an ``OperationalRunError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from app.agent_review.authoritative_check_policy_v2 import AuthoritativeCheckPolicyErrorV2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    FindingLifecycleRecordV2,
    PullRequestStateV2,
    RunOriginV2,
    TargetProfileV2,
)
from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError, acquire_authoritative_diff_v2
from app.agent_review.lifecycle_v2 import LifecycleAggregationError
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.payload_builder_v2 import PayloadBuilderError, build_chunk_payloads_from_profile_v2
from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2
from app.agent_review.payload_set_v2 import PayloadSetBindingError
from app.agent_review.profile_loader_v2 import TargetProfileLoadErrorV2, load_target_profile_v2
from app.agent_review.readiness_decision_v2 import ReadinessDecisionError
from app.agent_review.reference_source_v2 import ReferenceSourceError, resolve_reference_source_v2
from app.agent_review.required_check_provenance_v2 import RequiredCheckProvenanceErrorV2
from app.agent_review.required_check_readiness_v2 import RequiredCheckReadinessErrorV2
from app.agent_review.review_content_extraction_v2 import ExtractionBlockedError, extract_review_content_v2
from app.agent_review.review_content_v2 import (
    CONTENT_CHUNK_SET_MISMATCH_REASON_V2,
    CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2,
    ReviewContentV2,
)
from app.agent_review.review_readiness_emission_v2 import ReadinessEmissionError
from app.agent_review.review_transport_v2 import (
    ChunkReviewTransportV2,
    SyntheticReviewOutcomeV2,
    run_synthetic_review_v2,
)
from app.agent_review.run_assembly_v2 import RunAssemblyError, assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingError,
    SemanticGroupingPolicyV2,
    bind_semantic_grouping_policy_to_target_profile_v2,
)
from app.agent_review.synthesis_v2 import SynthesisErrorV2
from app.agent_review.toolrepo_identity_v2 import (
    ToolrepoIdentityError,
    ToolrepoSourceIdentityV2,
    establish_toolrepo_source_identity_v2,
)

__all__ = [
    "ASSEMBLY_BLOCKED_REASON_V2",
    "OperationalReviewOutcomeV2",
    "OperationalRunError",
    "PREPARATION_CHUNK_SET_MISMATCH_REASON_V2",
    "PreparedReviewRunV2",
    "prepare_operational_review_v2",
    "run_operational_review_v2",
]

# This module owns no taxonomy of its own beyond this one code, which names a
# condition no upstream authority is positioned to report: the manifest,
# payloads and content each validated against their own contracts, but the
# three do not describe the same chunk set. Every other refusal below reuses
# the exact `reason_code` its owning authority already raised.
PREPARATION_CHUNK_SET_MISMATCH_REASON_V2 = "operational_preparation_chunk_set_mismatch"

# `AssemblyBlockedReasonV2` (a plain dataclass, not an exception) is how
# `assemble_manifest_from_diff_v2` reports a WHOLE-run block; this is the
# fallback only for the structurally-impossible case where that field is
# `None` on a non-"assembled" outcome.
ASSEMBLY_BLOCKED_REASON_V2 = "assembly_blocked"

# The back half's own typed refusal families, wrapped once around the single
# `run_synthetic_review_v2` call -- see the module docstring's "Error
# surface" section for why each one is here and how its reachability was
# proven, not assumed.
_BACK_HALF_ERROR_FAMILIES_V2 = (
    SynthesisErrorV2,
    LifecycleAggregationError,
    ReadinessDecisionError,
    TargetProfileLoadErrorV2,
    AuthoritativeCheckPolicyErrorV2,
    RequiredCheckReadinessErrorV2,
    RequiredCheckProvenanceErrorV2,
    ReadinessEmissionError,
)


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
    # e.g. ``optional_artifact_missing:<id>`` -- the payload builder declares
    # these are never silently absorbed, so they are carried rather than
    # dropped on the floor between the front and back halves.
    payload_limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationalReviewOutcomeV2:
    """One complete operational run. Also internal, also never persisted.

    ``toolrepo_identity`` is the independently PROVEN toolrepo source
    identity this run executed under -- never the caller's bare declaration.
    ``review.synthesis`` is the exact ``SynthesisResultV2`` the back half
    computed exactly once and derived ``review.readiness`` from (see
    ``ONE_OPERATIONAL_SYNTHESIS_INVARIANT`` in ``review_transport_v2``).
    """

    toolrepo_identity: ToolrepoSourceIdentityV2
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
        file_diffs = acquire_authoritative_diff_v2(repo_root, base_sha=base_sha, head_sha=head_sha)
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
            # `assemble_manifest_from_diff_v2` now owns BOTH the type check
            # (a wrong type is a caller/programmer defect and raises a raw
            # `TypeError`, correctly left uncaught here) and the value check
            # (a well-typed non-positive budget is `RunAssemblyError
            # (run_assembly_budget_invalid)`) -- #271's own pre-validation of
            # this parameter is OBSOLETE_AFTER_272 and deliberately not
            # ported.
            max_lines_per_chunk=max_lines_per_chunk,
        )
    except RunAssemblyError as exc:
        raise OperationalRunError(exc.reason_code) from exc

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
        with resolve_reference_source_v2(
            repo_root=repo_root, head_sha=head_sha, profile=profile
        ) as reference_source:
            try:
                built = build_chunk_payloads_from_profile_v2(
                    manifest, profile=profile, repo_root=reference_source.root
                )
            except PayloadBuilderError as exc:
                # `payload_builder_v2`'s own public boundary already converts
                # its sibling `PayloadReferenceError` (a SIBLING family, not
                # a subclass) into this one, preserving its reason -- see
                # that module's docstring. Catching the sibling here too
                # would be exactly the enumeration habit #272 ended; if it
                # is ever observed escaping raw instead, that is
                # `STOP_OWNER_SURFACE_REOPENED`, fixed at the owner.
                raise OperationalRunError(exc.reason_code) from exc
    except ReferenceSourceError as exc:
        raise OperationalRunError(exc.reason_code) from exc

    payload_by_chunk_id = {item.chunk_id: item.payload for item in built}
    # The builder contracts that optional-artifact limitations are never
    # silently absorbed. Carry them so a caller can surface them.
    # De-duplicated deliberately: `build_chunk_payloads_from_profile_v2` reads
    # the reference set ONCE and reuses it for every chunk, so an
    # `optional_artifact_missing` limitation is repeated per chunk. It is one
    # fact about the run, not N facts.
    payload_limitations = tuple(
        sorted({limitation for item in built for limitation in item.limitations})
    )

    # Manifest <-> payload closure is an authority that already exists: reuse
    # it rather than re-deriving run_id/manifest_hash/chunk-set agreement.
    # `emit_payload_set_v2` now owns the empty-submission case itself
    # (`PayloadSetBindingError(payload_set_empty)`) -- #271's `if built:`
    # guard against a raw pydantic `ValidationError` here is
    # OBSOLETE_AFTER_272 and deliberately not ported.
    try:
        emit_payload_set_v2(manifest, [item.payload for item in built])
    except PayloadSetBindingError as exc:
        raise OperationalRunError(exc.reason_code) from exc

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
        payload_limitations=payload_limitations,
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
    declared_toolrepo_sha: str,
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
    executing_script: Path | None = None,
) -> OperationalReviewOutcomeV2:
    """Compose the front half onto the existing back half.

    ``toolrepo_identity`` is established FIRST, before the target profile is
    even loaded: `TOOLREPO_SOURCE_IDENTITY_INVARIANT` gates whether this
    engine may run a semantic review at all, independent of anything about
    the target being reviewed. Its proven ``toolrepo_sha`` (never the
    caller's bare declaration) is what reaches assembly's own run identity.

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

    try:
        toolrepo_identity = establish_toolrepo_source_identity_v2(
            declared_toolrepo_sha=declared_toolrepo_sha, executing_script=executing_script
        )
    except ToolrepoIdentityError as exc:
        raise OperationalRunError(exc.reason_code) from exc

    prepared = prepare_operational_review_v2(
        repo_root=repo_root,
        target_profile_root=target_profile_root,
        grouping_policy=grouping_policy,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=tested_merge_sha,
        pr_number=pr_number,
        toolrepo_sha=toolrepo_identity.toolrepo_sha,
        evidence_hash=evidence_hash,
        max_lines_per_chunk=max_lines_per_chunk,
        dlp_policy=dlp_policy,
    )

    # The back half reaches authorities with their own typed error families
    # (required-check policy/readiness/provenance, readiness decision,
    # readiness emission, synthesis, lifecycle). Each already carries a
    # stable `reason_code`; none may escape this composition as a raw
    # traceback -- especially not after the Router call has already been
    # made and paid for. See the module docstring's "Error surface" section
    # for how this exact family tuple was derived from the current call
    # graph, not copied from history.
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
    except _BACK_HALF_ERROR_FAMILIES_V2 as exc:
        raise OperationalRunError(exc.reason_code) from exc

    return OperationalReviewOutcomeV2(
        toolrepo_identity=toolrepo_identity, prepared=prepared, review=review
    )
