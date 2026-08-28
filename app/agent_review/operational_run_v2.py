"""`#200-E` Phase 3 -- the semantic child's operational composer (issue
#200, successor to the `FROZEN_FORENSIC` `#274`).

This module is Process B's business logic ONLY. It is imported and called
from inside the semantic child process after the process-boundary
environment sealing already applies (see
`tests/agent_review/test_semantic_child_process_boundary_v2.py`) -- it does
not itself construct that boundary, launch a subprocess, or know anything
about the toolrepo execution subject it is running from. That separation is
the architectural law this phase is built around: the OUTER bootstrap may
materialize the toolrepo subject and launch this process; it must never run
review semantics itself.

## Composition order, re-derived from CURRENT master owners, not ported
## from `#274`

```text
trusted profile load (load_target_profile_v2)
  -> semantic grouping policy bound to profile (bind_semantic_grouping_policy_to_target_profile_v2)
  -> controlled target subject materialized (materialize_controlled_target_subject_v2)
  -> head_sha checked out inside it (checkout_head_into_subject_v2)
  -> authoritative diff acquired FROM the subject (acquire_diff_v2)
  -> manifest assembled (assemble_manifest_from_diff_v2)
  -> controlled reference-material root built FROM the subject (materialize_controlled_reference_root_v2)
  -> chunk payloads built against that reference root (build_chunk_payloads_from_profile_v2)
  -> payload set emitted (emit_payload_set_v2)
  -> review content extracted FROM the subject (extract_review_content_v2)
  -> preparation closure verified
  -> each chunk executed through the existing transport choke point (execute_chunk_review_v2)
  -> synthesis computed EXACTLY ONCE (synthesize_chunk_results_v2)
  -> finding lifecycle aggregated (aggregate_finding_lifecycle_v2)
  -> readiness decided from that SAME synthesis object (compute_readiness_decision_v2)
  -> readiness emitted (produce_review_readiness_v2)
```

## What is NOT reused from `#274`

`reference_source_v2.py` is not ported -- §7's
`materialize_controlled_reference_root_v2` replaces it, built against the
controlled TARGET subject's object database rather than the target's
working tree. `_sealed_git_execution_v2.py` is not resurrected --
`_bounded_git_child_env_v2.py`'s allowlist plus the process-boundary
sealing proven in this phase's own test suite replace it. No target-pack
authority is reused as a review authority.

## Error surface, measured against the CURRENT call graph, not copied

Each front-half authority's own documented public exception family is
caught, and ONLY it -- never a bare `except Exception`, never
`except ValidationError`/`except OSError` at this composition layer
(those belong to the OWNER that raises them, if at all), never a
`getattr(exc, "reason_code", ...)` inference. A post-seal defect inside a
back-half owner (`run_synthetic_review_v2` et al.) is not caught here
either -- it must remain raw, per `#272`'s own two-epoch closure this
module builds on top of, unmodified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    PullRequestStateV2,
    ReviewReadinessV2,
    RunOriginV2,
    compute_run_id,
)
from app.agent_review.controlled_subject_v2 import (
    ControlledSubjectError,
    checkout_head_into_subject_v2,
    materialize_controlled_reference_root_v2,
    materialize_controlled_target_subject_v2,
)
from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError, acquire_diff_v2, parse_unified_diff
from app.agent_review.lifecycle_v2 import aggregate_finding_lifecycle_v2
from app.agent_review.parser_v2 import ParsedChunkResultV2
from app.agent_review.payload_builder_v2 import PayloadBuilderError, build_chunk_payloads_from_profile_v2
from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2
from app.agent_review.payload_set_v2 import PayloadSetV2
from app.agent_review.profile_loader_v2 import TargetProfileLoadErrorV2, load_target_profile_v2
from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
from app.agent_review.review_content_extraction_v2 import ExtractionBlockedError, extract_review_content_v2
from app.agent_review.review_readiness_emission_v2 import produce_review_readiness_v2
from app.agent_review.review_transport_v2 import ChunkReviewTransportV2, execute_chunk_review_v2
from app.agent_review.run_assembly_v2 import RunAssemblyError, assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingError,
    SemanticGroupingPolicyV2,
    bind_semantic_grouping_policy_to_target_profile_v2,
)
from app.agent_review.synthesis_v2 import synthesize_chunk_results_v2

OPERATIONAL_RUN_ASSEMBLY_BLOCKED_REASON_V2 = "operational_run_assembly_blocked"
OPERATIONAL_RUN_PREPARATION_CLOSURE_MISMATCH_REASON_V2 = (
    "operational_run_preparation_closure_mismatch"
)


class OperationalRunError(ValueError):
    """A refusal this composer names explicitly, for exactly the pre-seal
    failure classes it owns (assembly-blocked, preparation-closure
    mismatch). Every other typed family below is the FRONT-HALF OWNER's
    own -- not wrapped, not reclassified, propagated as-is so a caller
    still knows THAT it failed at a specific, already-documented boundary,
    not merely that this composer failed."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OperationalReviewInputsV2:
    """Caller material for one operational review run. Private, non-wire --
    never persisted or emitted; the wire-facing artifacts remain
    `ReviewReadinessV2` and friends, unchanged."""

    source_target_root: Path
    base_sha: str
    head_sha: str
    tested_merge_sha: str
    toolrepo_sha: str
    evidence_hash: str
    repo: str
    pr_number: int
    trusted_profile_root: Path
    grouping_policy: SemanticGroupingPolicyV2
    transport: ChunkReviewTransportV2
    pr_state: PullRequestStateV2
    origin: RunOriginV2
    max_lines_per_chunk: int = 200


def _verify_preparation_closure_v2(
    *,
    chunk_ids: frozenset[str],
    payload_by_chunk_id: dict[str, ChunkPayloadV2],
    content_chunk_ids: frozenset[str],
    content_payload_sha256_by_chunk_id: dict[str, str],
) -> None:
    """§11: before transport, the manifest/payload/content chunk sets must
    be identical, and every chunk's content-declared `payload_sha256` must
    match the actually-built payload's own. A missing payload must never
    first surface as a downstream `KeyError` -- this is checked explicitly,
    here, before any chunk reaches `execute_chunk_review_v2`."""

    payload_chunk_ids = frozenset(payload_by_chunk_id)
    if not (chunk_ids == payload_chunk_ids == content_chunk_ids):
        raise OperationalRunError(OPERATIONAL_RUN_PREPARATION_CLOSURE_MISMATCH_REASON_V2)
    for chunk_id in chunk_ids:
        if content_payload_sha256_by_chunk_id[chunk_id] != payload_by_chunk_id[chunk_id].payload_sha256:
            raise OperationalRunError(OPERATIONAL_RUN_PREPARATION_CLOSURE_MISMATCH_REASON_V2)


def run_operational_review_v2(inputs: OperationalReviewInputsV2) -> ReviewReadinessV2:
    """Run one complete operational review from real inputs to a real,
    honest `ReviewReadinessV2`. Never fabricates check authority: absent an
    authoritative green required check, the terminal readiness state is
    whatever `compute_readiness_decision_v2`/`produce_review_readiness_v2`
    honestly derive (typically `manual_required`/`blocked_pipeline`), never
    forced to `ready`.
    """

    # load_target_profile_v2 raises TargetProfileLoadErrorV2;
    # bind_semantic_grouping_policy_to_target_profile_v2 raises
    # SemanticGroupingError. Both are the owning authority's own typed
    # family and propagate unmodified -- not caught, not wrapped.
    profile = load_target_profile_v2(inputs.trusted_profile_root)
    bind_semantic_grouping_policy_to_target_profile_v2(inputs.grouping_policy, profile)

    with materialize_controlled_target_subject_v2(
        inputs.source_target_root, base_sha=inputs.base_sha, head_sha=inputs.head_sha
    ) as target_subject:
        # checkout_head_into_subject_v2 raises ControlledSubjectError;
        # acquire_diff_v2 raises DiffAcquisitionError;
        # assemble_manifest_from_diff_v2 raises RunAssemblyError. Each
        # propagates unmodified.
        checkout_head_into_subject_v2(target_subject)
        diff_text = acquire_diff_v2(
            target_subject.root, base_sha=inputs.base_sha, head_sha=inputs.head_sha
        )
        file_diffs = parse_unified_diff(diff_text)
        assembly = assemble_manifest_from_diff_v2(
            file_diffs,
            profile=profile,
            grouping_policy=inputs.grouping_policy,
            repo=inputs.repo,
            pr_number=inputs.pr_number,
            base_sha=inputs.base_sha,
            head_sha=inputs.head_sha,
            tested_merge_sha=inputs.tested_merge_sha,
            toolrepo_sha=inputs.toolrepo_sha,
            evidence_hash=inputs.evidence_hash,
            max_lines_per_chunk=inputs.max_lines_per_chunk,
        )
        if assembly.state != "assembled" or assembly.manifest is None:
            raise OperationalRunError(OPERATIONAL_RUN_ASSEMBLY_BLOCKED_REASON_V2)
        manifest = assembly.manifest

        declared_paths = tuple(artifact.path for artifact in profile.artifacts) + tuple(
            contract.path for contract in profile.contracts
        )
        # materialize_controlled_reference_root_v2 raises
        # ControlledSubjectError; build_chunk_payloads_from_profile_v2
        # raises PayloadBuilderError. Each propagates unmodified.
        reference_root = materialize_controlled_reference_root_v2(
            target_subject, declared_paths=declared_paths
        )
        built = build_chunk_payloads_from_profile_v2(
            manifest, profile=profile, repo_root=reference_root
        )
        payload_by_chunk_id: dict[str, ChunkPayloadV2] = {b.chunk_id: b.payload for b in built}
        payload_set: PayloadSetV2 = emit_payload_set_v2(manifest, [b.payload for b in built])

        # extract_review_content_v2 raises ExtractionBlockedError,
        # propagated unmodified.
        content = extract_review_content_v2(
            repo_root=target_subject.root,
            base_sha=inputs.base_sha,
            head_sha=inputs.head_sha,
            manifest=manifest,
            payload_sha256_by_chunk_id={
                chunk_id: payload.payload_sha256 for chunk_id, payload in payload_by_chunk_id.items()
            },
            target_profile=profile,
        )

        content_by_chunk_id = {chunk.chunk_id: chunk for chunk in content.chunks}
        _verify_preparation_closure_v2(
            chunk_ids=frozenset(chunk.chunk_id for chunk in manifest.chunks),
            payload_by_chunk_id=payload_by_chunk_id,
            content_chunk_ids=frozenset(content_by_chunk_id),
            content_payload_sha256_by_chunk_id={
                chunk_id: chunk.payload_sha256 for chunk_id, chunk in content_by_chunk_id.items()
            },
        )

        run_id = compute_run_id(manifest.identity)
        chunk_results: list[ParsedChunkResultV2] = []
        for chunk_id, payload in payload_by_chunk_id.items():
            outcome = execute_chunk_review_v2(
                content_by_chunk_id[chunk_id],
                run_id=run_id,
                head_sha=inputs.head_sha,
                payload=payload,
                transport=inputs.transport,
            )
            if outcome.state == "bound" and outcome.result is not None:
                chunk_results.append(outcome.result)

    # Everything below is post-seal (§13-adjacent for THIS composer): a
    # defect here is a repository bug, not an operational refusal, and
    # deliberately escapes raw -- no try/except wraps synthesis, lifecycle,
    # readiness decision, or readiness emission.
    synthesis = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=chunk_results, evaluated_head_sha=inputs.head_sha
    )
    lifecycle_records, _provenance_by_finding = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=chunk_results, evaluated_head_sha=inputs.head_sha
    )
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=profile.policies
    )
    empty_snapshot = parse_authoritative_ci_snapshot_v2(
        _canonical_json_bytes_v2(_empty_authoritative_check_snapshot_dict_v2(inputs))
    )
    readiness = produce_review_readiness_v2(
        decision=decision,
        findings=lifecycle_records,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=inputs.pr_state,
        checks=[],
        provenance=[],
        origin=inputs.origin,
        snapshot=empty_snapshot,
        toolchain_digest=inputs.toolrepo_sha,
        target_profile_root=str(inputs.trusted_profile_root),
    )
    return readiness


def _empty_authoritative_check_snapshot_dict_v2(inputs: OperationalReviewInputsV2) -> dict:
    """No live authoritative-check acquisition happens in this
    provider-free slice (owned separately by
    `aiops-acquire-authoritative-checks-v2.py`). An EMPTY submission never
    reaches the promotion path at all -- `verify_required_check_
    provenance_set_v2`'s loop is vacuous for empty `checks` -- so the
    snapshot's own content beyond parsing validity is irrelevant here; its
    absence is exactly why the honest terminal readiness state below is
    non-ready, not a fabricated green."""

    return {
        "schema_id": "agent-review.authoritative-check-snapshot.v2",
        "schema_version": 2,
        "source": "aiops-acquire-authoritative-checks",
        "acquisition": {
            "acquired_by": "aiops-acquire-authoritative-checks-v2",
            "api_host": "api.github.com",
            "repository": inputs.repo,
            "head_sha": inputs.head_sha,
        },
        "observations": [],
        "tested_merge_sha": inputs.tested_merge_sha,
        "tested_merge_parents": [inputs.base_sha],
        "observation_bytes_digest": "0" * 64,
    }


def _canonical_json_bytes_v2(value: dict) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
