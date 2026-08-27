"""C2 -- emit a real ReviewReadinessV2 artifact (issue #130, child of
tracker #108). Extended by `#201-C` (plan rev.2.1) to be the single
production path through which required-check authority connects to
readiness.

Folds C1/#127's ``ReadinessDecisionV2`` (state, reason codes, blockers,
bridged coverage, pipeline assessment) together with identity, the full
finding lifecycle, ``pr_state``, and a legitimated required-check set into
a real ``ReviewReadinessV2``. Per the tracker's own rule -- "``ReviewReadinessV2``
é a AUTORIDADE: o computador decide o estado e deixa o contrato reprovar.
Proibido reimplementar as invariantes de ``validate_state_invariants`` fora
do contrato" -- nothing in this module re-checks anything
``ReviewReadinessV2.validate_state_invariants`` (``contracts_v2.py``)
already enforces. It only assembles the constructor call; the contract's
own validator is the sole authority on whether the result is well-formed,
and fails closed if it is not.

Caller-visible ``ready`` preconditions are evaluated BEFORE construction and
refused as ``ReadinessEmissionError`` with a reason that names the unmet rule.
The validator remains the authority on whether the artifact is well-formed,
and its rules are consulted, never restated. A ``ValidationError`` from
construction after that seal is a derivation defect and escapes raw.

## The single production constructor path (`#201-C`, R2)

```text
produce_review_readiness_v2                 <- the ONLY public production entry point
  -> _verify_and_assess_required_checks_v2   (required_check_readiness_v2, THE C0 frontier)
  -> _apply_required_check_assessment_v2     (readiness_decision_v2, precedence)
  -> _assemble_review_readiness_v2           (this module, pure, now internal)
       -> ReviewReadinessV2(...)             <- the ONLY construction site
```

``_assemble_review_readiness_v2`` -- this module's original ``emit_review_
readiness_v2`` -- is deliberately no longer public. Nothing about the pure
assembly step itself needed to change (it still does not decide authority,
still does not re-implement the frozen contract's invariants), but a
caller that could reach it directly could hand it a hand-built ``checks``
array or a hand-built ``RequiredCheckReadinessAssessmentV2`` and have
either treated as legitimate -- reopening, one layer above
``review_transport_v2``/the quality-gate CLI, precisely the bypass
``#201-C0`` closed one layer below. ``produce_review_readiness_v2`` is
therefore the only function in this codebase permitted to call it, and it
always calls the real, unpatched C0 verifier first, in the same call, with
no ``except RequiredCheckProvenanceErrorV2`` anywhere in between. See
``tests/agent_review/test_required_check_readiness_arch_v2.py`` for the
AST-level proof.

## What `#201-C` authenticates, and what it deliberately still does not

``produce_review_readiness_v2`` authenticates required checks: every
``RequiredCheckResultV2`` that reaches ``ReviewReadinessV2.checks`` has
passed ``#201-C0``'s re-derivation against an acquired snapshot, a
base-owned policy, and the run identity. It does **not** authenticate
where ``decision``/``findings`` came from. ``ReadinessDecisionV2`` remains,
by C1's own design, "plain, freely constructible data value... not a wire
contract with its own schema/hash" -- the only binding this module (via
``_assemble_review_readiness_v2``) enforces on it is REPLAY protection
(``decision.run_id``/``manifest_hash`` must match ``evaluated_identity``),
never origin. This is a preexisting trust assumption inherited unchanged
from C1/C2 (`#127`/`#130`), not created or widened by `#201-C`: today it is
provably inert, because with no positive required-check authority reaching
this function (`#201-C0`'s ``verify_independent_semantic_judge_v2``
refuses unconditionally, and ``TRUSTED_HOST_PROMOTION`` has no production
caller), a fabricated ``decision`` claiming ``READY`` is still narrowed by
``_apply_required_check_assessment_v2`` the moment any required check is
missing or unestablished -- which is always, today. If a future slice
(`#203`/CT104 bringing positive authority online) ever makes it possible
for subject-controlled code to influence ``decision``/``findings`` in a way
that becomes reachable through this function, that is a NEW trust-boundary
defect requiring its own stop-and-decide, not something for this module to
silently absorb.

## ``pr_state``/``checks``/``provenance``/``snapshot`` are caller-supplied,
## not acquired here

Real acquisition of a PR's live ``pr_state`` and of the authoritative-check
snapshot (e.g. via ``gh pr view``/the CI API) is a live network operation.
Every prior slice in this convergence effort (#103's git-subprocess diff
acquisition, #129's manifest assembly, #131's artifact/contract reading,
`#201-C0`'s own acquirer/assembler split) has drawn the identical boundary:
the ACQUISITION step stays separate from the ASSEMBLY/verification step.
This module mirrors that same separation -- every input is accepted as
already acquired by whatever caller has legitimate, granted network/GitHub
read access in its own execution context.

Deliberately out of scope, per the issue and per `#201-C`: publishing
anything to GitHub; altering `quality_gate.py` v1 or
`scripts/aiops-review-quality-gate.py`; anything about Path A/Path B
authority itself, which remains entirely `#201-C0`'s.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from app.agent_review.authoritative_ci_snapshot_v2 import AuthoritativeCheckSnapshotV2
from app.agent_review.contracts_v2 import (
    FindingLifecycleRecordV2,
    PullRequestStateV2,
    ReadinessStateV2,
    RequiredCheckResultV2,
    ReviewReadinessV2,
    RunIdentityV2,
    evaluate_readiness_submitted_material_v2,
    evaluate_ready_preconditions_v2,
    RunOriginV2,
    compute_run_id,
)
from app.agent_review.readiness_decision_v2 import ReadinessDecisionV2, _apply_required_check_assessment_v2
from app.agent_review.required_check_provenance_v2 import RequiredCheckProvenanceV2
from app.agent_review.required_check_readiness_v2 import _verify_and_assess_required_checks_v2

REVIEW_READINESS_SOURCE_V2 = "aiops-review-quality-gate"

READINESS_EMISSION_DECISION_PROVENANCE_MISMATCH_REASON_V2 = "readiness_emission_decision_provenance_mismatch"


class ReadinessEmissionError(ValueError):
    """Raised when a `ReadinessDecisionV2` is emitted against an identity it
    was not actually computed for. Carries a stable `reason_code` only --
    never decision or identity content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def produce_review_readiness_v2(
    *,
    decision: ReadinessDecisionV2,
    findings: Sequence[FindingLifecycleRecordV2],
    identity: RunIdentityV2,
    evaluated_identity: RunIdentityV2,
    pr_state: PullRequestStateV2,
    checks: Sequence[RequiredCheckResultV2],
    provenance: Sequence[RequiredCheckProvenanceV2],
    origin: RunOriginV2,
    snapshot: AuthoritativeCheckSnapshotV2,
    toolchain_digest: str,
    target_profile_root: str,
) -> ReviewReadinessV2:
    """THE single production entry point for constructing a
    ``ReviewReadinessV2``. ``checks``/``provenance`` are unauthenticated
    CLAIMS -- exactly as trustworthy as any other caller-supplied bytes --
    until ``_verify_and_assess_required_checks_v2`` (``#201-C0``'s own
    verifier, called from here, always, first) accepts them without
    raising. A forged or invalid submission's
    ``RequiredCheckProvenanceErrorV2`` propagates uncaught: it is never
    converted into a ``manual_required`` artifact, and no artifact is
    produced at all.

    ``required_check_names`` is not a parameter here, deliberately: it is
    derived, inside ``_verify_and_assess_required_checks_v2``, exclusively
    from a ``TargetProfileV2`` loaded fresh from ``target_profile_root``
    and bound to ``evaluated_identity.profile_hash``. See that function's
    own docstring.

    Adversarial review finding, confirmed and fixed: ``STALE`` is checked
    HERE, before the ``#201-C0`` call, not only inside ``_apply_required_
    check_assessment_v2``. A previous version called ``_verify_and_assess_
    required_checks_v2`` unconditionally, so an EMPTY submission alongside
    a genuinely ``STALE`` decision could still be refused if
    ``target_profile_root`` -- a live, base/default checkout -- had moved
    since ``evaluated_identity`` was computed, contradicting the
    documented guarantee that STALE evidence is never even asked a
    required-check question. A ``STALE`` decision now never reaches the
    ``#201-C0`` boundary at all: identity/HEAD divergence already makes
    any required-check claim non-current, so there is nothing legitimate
    to verify it against.
    """

    # ---------------- EPOCH 1: caller material ----------------
    #
    # `decision`, `findings`, `checks`, `identity`, `evaluated_identity` and
    # `pr_state` were all SUBMITTED to this function; it derived none of them.
    # A `--decision` JSON and a `compute_readiness_decision_v2` output arrive
    # as the same argument and are equally caller-material AT THIS BOUNDARY --
    # which is why no sealed carrier is needed to tell them apart. Provenance
    # beyond this point belongs to whoever produced the decision.
    unmet = evaluate_readiness_submitted_material_v2(
        decision=decision,
        findings=findings,
        checks=checks,
        identity=identity,
        evaluated_identity=evaluated_identity,
        pr_state=pr_state,
    )
    if unmet is not None:
        raise ReadinessEmissionError(unmet)

    # ------------------------- SEAL -------------------------
    #
    # Below, the required-check assessment and its adjusted decision are
    # TRANSFORMATION output, and run/head identity fields are DERIVED. A
    # `ValidationError` from either is a defect in this repository and escapes.
    if decision.state is ReadinessStateV2.STALE:
        return _assemble_review_readiness_v2(
            decision=decision,
            findings=findings,
        identity=identity,
        evaluated_identity=evaluated_identity,
        pr_state=pr_state,
            checks=(),
        )

    assessment = _verify_and_assess_required_checks_v2(
        checks=checks,
        provenance=provenance,
        identity=evaluated_identity,
        origin=origin,
        snapshot=snapshot,
        toolchain_digest=toolchain_digest,
        target_profile_root=target_profile_root,
    )
    adjusted_decision = _apply_required_check_assessment_v2(decision=decision, assessment=assessment)
    return _assemble_review_readiness_v2(
        decision=adjusted_decision,
        findings=findings,
        identity=identity,
        evaluated_identity=evaluated_identity,
        pr_state=pr_state,
        checks=assessment.checks,
    )


def _assemble_review_readiness_v2(
    *,
    decision: ReadinessDecisionV2,
    findings: Sequence[FindingLifecycleRecordV2],
    identity: RunIdentityV2,
    evaluated_identity: RunIdentityV2,
    pr_state: PullRequestStateV2,
    checks: Sequence[RequiredCheckResultV2],
) -> ReviewReadinessV2:
    """Assemble a real ``ReviewReadinessV2`` from an already-decided
    ``decision`` plus identity/``pr_state``/``checks``/findings.

    Internal since `#201-C` (R2): the only caller in this codebase is
    ``produce_review_readiness_v2``, immediately above, which always
    supplies ``checks`` as ``assessment.checks`` -- the same verified set
    ``#201-C0``'s verifier accepted (canonicalized by ``check_name``, never
    left in caller-submission order -- see ``required_check_readiness_v2``'s
    own "Canonical check order" section), never a raw caller-supplied
    array. See the module docstring for why this function no longer
    accepts one directly.

    ``identity`` and ``evaluated_identity`` are the SAME object except when
    the caller has independently determined staleness (the same case
    ``decision.state == STALE`` already represents, per C1's own module
    docstring) -- this function does not itself decide staleness, only
    reflects whatever the caller (and, transitively, C1's
    ``stale_reason_codes`` parameter) already decided.

    Raises ``ReadinessEmissionError`` with the rule-naming reason a caller-
    visible ``ready`` precondition was not met (``ready_requires_open_pr``,
    ``ready_requires_green_checks``, ...). Those preconditions are evaluated
    BEFORE the artifact is constructed, by the single authority in
    ``contracts_v2`` that ``validate_state_invariants`` also consults, so the
    rules are never restated here.

    After that seal, a ``ValidationError`` from construction means derivation
    produced an invalid artifact from validated material -- a defect in this
    repository, and it escapes raw.

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

    # Derivation happens HERE, before the seal, so a defect in it cannot be
    # mistaken for a caller problem: `compute_run_id` is this module's only
    # computation, and a failure in it escapes raw.
    run_id = compute_run_id(identity)
    evaluated_run_id = compute_run_id(evaluated_identity)

    # This assembler is entirely POST-SEAL. On the main path its `decision`
    # and `checks` are TRANSFORMATION output -- `_apply_required_check_
    # assessment_v2`'s adjusted decision and the assessment's own verified
    # checks -- not caller material. Converting a `ValidationError` here would
    # therefore report a transformation defect as an operator's fault, which
    # is the laundering that falsified the previous two designs.
    #
    # Caller material is validated in `produce_review_readiness_v2`, before
    # the assessment runs. Nothing is converted below.
    return ReviewReadinessV2(
        schema_id="agent-review.review-readiness.v2",
        schema_version=2,
        source=REVIEW_READINESS_SOURCE_V2,
        run_id=run_id,
        identity=identity,
        evaluated_run_id=evaluated_run_id,
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
