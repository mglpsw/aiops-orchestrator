"""C2 -- emit a real ReviewReadinessV2 artifact (issue #130, child of
tracker #108).

Folds C1/#127's ``ReadinessDecisionV2`` (state, reason codes, blockers,
bridged coverage, pipeline assessment) together with identity, the full
finding lifecycle, ``pr_state``, and ``checks`` into a real
``ReviewReadinessV2``. Per the tracker's own rule -- "``ReviewReadinessV2``
é a AUTORIDADE: o computador decide o estado e deixa o contrato reprovar.
Proibido reimplementar as invariantes de ``validate_state_invariants`` fora
do contrato" -- this module does not re-check anything
``ReviewReadinessV2.validate_state_invariants`` (``contracts_v2.py``)
already enforces. It only assembles the constructor call; the contract's
own validator is the sole authority on whether the result is well-formed,
and raises ``pydantic.ValidationError`` fail-closed if it is not.

## ``pr_state``/``checks`` are caller-supplied, not acquired here

Real acquisition of a PR's live ``pr_state``/``checks`` (e.g. via ``gh pr
view``/``gh pr checks``) is a live GitHub network operation. Every prior
slice in this convergence effort (#103's git-subprocess diff acquisition,
#129's manifest assembly, #131's artifact/contract reading) has drawn the
identical boundary: the ACQUISITION step (reading from something external)
stays separate from and untested-live-by the ASSEMBLY step (a pure function
over already-acquired values) -- #129's `assemble_manifest_from_diff_v2`
accepts already-parsed `ParsedFileDiffV2` tuples rather than calling
`acquire_authoritative_diff_v2` itself, for exactly this reason. This
module mirrors that same separation: `pr_state`/`checks` are accepted as
parameters, already acquired by whatever caller has legitimate, granted
network/GitHub read access in its own execution context. A live
`gh`-based adapter is explicitly deferred, out of scope for this offline,
CT104-scoped slice -- wiring one is future work requiring its own grant for
real network/GitHub access, not implied by this issue.

Deliberately out of scope, per the issue: publishing anything to GitHub;
altering `quality_gate.py` v1 or `scripts/aiops-review-quality-gate.py`.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.agent_review.contracts_v2 import (
    FindingLifecycleRecordV2,
    PullRequestStateV2,
    RequiredCheckResultV2,
    ReviewReadinessV2,
    RunIdentityV2,
    compute_run_id,
)
from app.agent_review.readiness_decision_v2 import ReadinessDecisionV2

REVIEW_READINESS_SOURCE_V2 = "aiops-review-quality-gate"

READINESS_EMISSION_DECISION_PROVENANCE_MISMATCH_REASON_V2 = "readiness_emission_decision_provenance_mismatch"


class ReadinessEmissionError(ValueError):
    """Raised when a `ReadinessDecisionV2` is emitted against an identity it
    was not actually computed for. Carries a stable `reason_code` only --
    never decision or identity content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def emit_review_readiness_v2(
    *,
    decision: ReadinessDecisionV2,
    findings: Sequence[FindingLifecycleRecordV2],
    identity: RunIdentityV2,
    evaluated_identity: RunIdentityV2,
    pr_state: PullRequestStateV2,
    checks: Sequence[RequiredCheckResultV2],
) -> ReviewReadinessV2:
    """Assemble a real ``ReviewReadinessV2`` from C1's decision plus the
    caller-supplied identity/``pr_state``/``checks``/findings.

    ``identity`` and ``evaluated_identity`` are the SAME object except when
    the caller has independently determined staleness (the same case
    ``decision.state == STALE`` already represents, per C1's own module
    docstring) -- this function does not itself decide staleness, only
    reflects whatever the caller (and, transitively, C1's
    ``stale_reason_codes`` parameter) already decided.

    Raises ``pydantic.ValidationError`` -- never wrapped, never
    re-implemented -- if the assembled combination does not satisfy
    ``ReviewReadinessV2.validate_state_invariants``. That validator is the
    authority; this function is not.

    Before that, raises ``ReadinessEmissionError`` if ``decision``'s own
    ``run_id``/``manifest_hash`` provenance does not match
    ``evaluated_identity`` -- ``evaluated_identity`` is specifically the
    identity the decision was actually computed against (the same one
    C1's own ``compute_readiness_decision_v2`` received as ``manifest``),
    never ``identity`` (which may deliberately diverge from it in the
    ``STALE`` case). Without this check, a `ready` decision computed for
    one run could be combined with an unrelated run's identity/findings/
    checks at emission time with no invariant able to detect the replay --
    a real gap a Codex review of #145 found.
    """

    if decision.run_id != compute_run_id(evaluated_identity) or decision.manifest_hash != evaluated_identity.manifest_hash:
        raise ReadinessEmissionError(READINESS_EMISSION_DECISION_PROVENANCE_MISMATCH_REASON_V2)

    return ReviewReadinessV2(
        schema_id="agent-review.review-readiness.v2",
        schema_version=2,
        source=REVIEW_READINESS_SOURCE_V2,
        run_id=compute_run_id(identity),
        identity=identity,
        evaluated_run_id=compute_run_id(evaluated_identity),
        evaluated_identity=evaluated_identity,
        head_sha=identity.head_sha,
        evaluated_head_sha=evaluated_identity.head_sha,
        pr_state=pr_state,
        checks=list(checks),
        coverage=decision.coverage,
        pipeline=decision.pipeline,
        state=decision.state,
        reason_codes=list(decision.reason_codes),
        blockers=list(decision.blockers),
        findings=list(findings),
    )
