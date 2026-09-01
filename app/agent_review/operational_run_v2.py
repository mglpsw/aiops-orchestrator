"""`#200-F` §12/§13 -- the operational composition, re-derived.

Deliberately **not** a port of `#276`'s composer. That module was shaped by the
mechanisms this slice retired -- an exception tuple at the boundary, private
argv flags for inner authority, and a blanket refusal whenever the assembly
excluded any path. Re-deriving from the new authorities produces a different
shape, and copying it would have carried the old shape's assumptions in.

## Order

::

    validated public inputs        (ingress ran -- proved by the argument type)
      -> trusted profile
      -> controlled target subject (committed bytes, severed)
      -> changed-scope assessment  (every path dispositioned)
      -> reviewable fragments / manifest
      -> payloads
      -> review content
      -> transport                 (offline or Router-format; never live here)
      -> range-aware binding       (path + fragment + range, before binding)
      -> ONE synthesis
      -> readiness (fragment coverage + scope completeness)

The toolrepo execution subject and the inner-control channel sit *outside*
this module, in the CLI: by the time this function runs, the process is
already executing from the controlled subject. Putting them here would mean
the composer authenticated the code it is itself part of.

## Two things this module refuses to do

**It does not accept raw strings.** The parameter type is
``ValidatedPublicInputsV2``, which only ingress can produce. "Did anyone
validate this?" is answered by the type system rather than by convention.

**It does not emit ``ready`` when total scope is incomplete.** That gate needs
no published vocabulary and is enforced here. What it cannot yet do is *say*
so in the emitted artifact -- see ADR-200F and the `STOP_SCOPE_CONTRACT_
REQUIRED` verdict. The distinction is recorded in this run document, which is
a product output rather than a published contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    ReadinessStateV2,
    TargetProfileV2,
)
from app.agent_review.semantic_grouping_policy_v2 import SemanticGroupingPolicyV2
from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.operational_ingress_v2 import ValidatedPublicInputsV2
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2
from app.agent_review.operational_result_binding_v2 import (
    bind_offline_response_with_range_authority_v2,
)
from app.agent_review.operational_scope_v2 import (
    ScopeAssessmentV2,
    assess_changed_scope_v2,
)
from app.agent_review.parser_v2 import parse_bound_chunk_response_v2
from app.agent_review.payload_builder_v2 import build_chunk_payloads_v2
from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.synthesis_v2 import synthesize_chunk_results_v2

__all__ = [
    "OPERATIONAL_RUN_ASSEMBLY_BLOCKED_REASON_V2",
    "OPERATIONAL_RUN_MISSING_CHUNK_RESPONSE_REASON_V2",
    "OPERATIONAL_RUN_MUST_REVIEW_UNREVIEWABLE_REASON_V2",
    "OperationalRunError",
    "OperationalRunResultV2",
    "execute_operational_run_v2",
]


OPERATIONAL_RUN_ASSEMBLY_BLOCKED_REASON_V2 = "operational_run_assembly_blocked"
OPERATIONAL_RUN_MISSING_CHUNK_RESPONSE_REASON_V2 = "operational_run_missing_chunk_response"
OPERATIONAL_RUN_MUST_REVIEW_UNREVIEWABLE_REASON_V2 = "operational_run_must_review_unreviewable"


class OperationalRunError(ExpectedOperationalRefusalV2, ValueError):
    """A run could not be composed. Content-free ``reason_code`` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OperationalRunResultV2:
    """One run's product.

    ``synthesis`` is retained so callers can assert the one-synthesis
    invariant by object identity rather than by re-deriving an equal value.
    """

    manifest: ManifestV2
    scope: ScopeAssessmentV2
    synthesis: Any
    readiness_state: ReadinessStateV2
    reason_codes: tuple[str, ...]
    findings: tuple[Any, ...]

    @property
    def ready(self) -> bool:
        return self.readiness_state is ReadinessStateV2.READY


#: A transport is a function from a payload to a raw response envelope. The
#: composer never performs I/O itself, so the *same* composition runs offline,
#: against a mocked HTTP boundary, or -- under a grant this slice does not
#: have -- against a live Router.
ReviewTransportV2 = Callable[[ChunkPayloadV2], Mapping[str, Any] | str | bytes]


def execute_operational_run_v2(
    *,
    inputs: ValidatedPublicInputsV2,
    profile: TargetProfileV2,
    grouping_policy: SemanticGroupingPolicyV2,
    file_diffs: Sequence[ParsedFileDiffV2],
    transport: ReviewTransportV2,
    evidence_hash: str,
    max_lines_per_chunk: int = 400,
) -> OperationalRunResultV2:
    """Compose one review run and return its readiness.

    Every refusal raised from here is a member of the operational refusal
    family, so the CLI catches structurally and never enumerates.
    """
    # Scope is assessed BEFORE assembly, from the same diffs assembly will
    # see. Assessing it afterwards would mean asking the assembly what
    # changed, and the assembly only knows what it could turn into fragments
    # -- which is precisely the blind spot authority C exists to remove.
    scope = assess_changed_scope_v2(file_diffs=file_diffs, profile=profile)

    if scope.blocked:
        # Fail closed, with a reason code that says what happened. The
        # assembly would also refuse this run -- a must-review path with no
        # fragments blocks it -- but under a generic
        # `operational_run_assembly_blocked`, which tells an operator to go
        # looking at chunking when the real cause is that a path the target
        # declared must-review carried nothing reviewable. Deciding it here,
        # from the scope authority that actually knows, keeps the diagnosis
        # accurate and does not depend on assembly's incidental ordering.
        raise OperationalRunError(OPERATIONAL_RUN_MUST_REVIEW_UNREVIEWABLE_REASON_V2)

    outcome = assemble_manifest_from_diff_v2(
        file_diffs,
        profile=profile,
        grouping_policy=grouping_policy,
        repo=inputs.repo,
        pr_number=inputs.pr_number,
        base_sha=inputs.base_sha,
        head_sha=inputs.head_sha,
        tested_merge_sha=inputs.tested_merge_sha,
        toolrepo_sha=inputs.toolchain_digest[:40],
        evidence_hash=evidence_hash,
        max_lines_per_chunk=max_lines_per_chunk,
    )
    if outcome.state != "assembled" or outcome.manifest is None:
        raise OperationalRunError(OPERATIONAL_RUN_ASSEMBLY_BLOCKED_REASON_V2)
    manifest = outcome.manifest

    # `#276` refused the whole run here whenever excluded_paths was non-empty.
    # It is now an input to the scope assessment's completeness, not a veto:
    # a pure rename must not deny an otherwise complete review.
    parsed_results = []
    for built in build_chunk_payloads_v2(manifest):
        raw_response = transport(built.payload)
        if raw_response is None:
            raise OperationalRunError(OPERATIONAL_RUN_MISSING_CHUNK_RESPONSE_REASON_V2)
        bound = bind_offline_response_with_range_authority_v2(
            envelope=raw_response, payload=built.payload, manifest=manifest
        )
        parsed_results.append(parse_bound_chunk_response_v2(bound))

    # ONE synthesis. The object produced here is the object that feeds
    # readiness and the object whose findings are returned -- asserted by
    # identity in the tests, because an equal-but-distinct value would mean a
    # second aggregation had happened somewhere.
    synthesis = synthesize_chunk_results_v2(
        manifest=manifest,
        chunk_results=parsed_results,
        evaluated_head_sha=inputs.head_sha,
    )

    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=profile.policies
    )

    state = decision.state
    reason_codes = tuple(reason.value for reason in decision.reason_codes)

    if state is ReadinessStateV2.READY and not scope.scope_complete:
        # Scope completeness is a separate question from fragment coverage,
        # and the artifact vocabulary cannot yet express it (ADR-200F). What
        # can be done without a published-contract change is refuse to claim
        # ready, which is the part that actually protects a consumer.
        state = ReadinessStateV2.MANUAL_REQUIRED

    return OperationalRunResultV2(
        manifest=manifest,
        scope=scope,
        synthesis=synthesis,
        readiness_state=state,
        reason_codes=reason_codes,
        findings=synthesis.findings,
    )
