"""`#200-F` §8 -- can the published contracts express scope incompleteness?

The grant requires this question to be *proved* before authority C is
implemented, and to yield ``STOP_SCOPE_CONTRACT_REQUIRED`` if the answer is
no. The determination is executable and lives here permanently, so it is
re-checked on every run rather than resting on a paragraph someone wrote once.
`#276` demonstrated what happens when a written justification is the only
evidence: one of its justifications was factually false and its control passed
over it.

The scenario under test is the ordinary one:

    two source files changed and fully reviewed,
    plus one pure rename that carries no reviewable material.

The fragment review is **complete**. The total changed scope is **not fully
accounted for** by the emitted artifact. The question is whether
``agent-review.review-readiness.v2`` -- a *published* schema -- can say so.

Verdict reached here: **no, not without semantic distortion.** The finding is
recorded in ``docs/adr/ADR-200F-SCOPE-COMPLETENESS-CONTRACT.md``.
"""

from __future__ import annotations

import pydantic
import pytest

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    CoverageDegradationReasonV2,
    CoverageDegradationV2,
    CoverageStateV2,
    ReadinessReasonV2,
    ReviewReadinessV2,
)

_REVIEWED_FILES_V2 = ("src/a.py", "src/b.py")
_UNREVIEWABLE_CHANGED_FILES_V2 = ("src/renamed.py",)


def test_coverage_reports_complete_while_a_changed_path_is_invisible() -> None:
    """The root hazard, shown at the contract level.

    Scope note, corrected after review: this constructs a ``ChunkCoverageV2``
    by hand and shows the contract *accepts* a complete-looking coverage that
    omits a changed path. It does **not** demonstrate that the pipeline
    produces one -- the ADR previously cited it as if it did, which was
    circular. The pipeline-level demonstration is
    ``test_operational_run_v2.py::test_the_false_ready_path_is_closed``, which
    grew out of a real false ``ready`` found by adversarial review.


    ``ChunkCoverageV2`` is computed over ``expected_files``, which holds paths
    that produced reviewable fragments. A changed path that produces none is
    simply absent, and every partition invariant is satisfied without it. The
    contract therefore certifies ``complete`` for a run that never looked at
    part of the change.

    This is why an internal scope authority is required at all, and why
    `#276`'s instinct was right even though its remedy -- refuse every run
    with a non-empty ``excluded_paths`` -- was aimed at the wrong level.
    """
    coverage = ChunkCoverageV2(
        status=CoverageStateV2.COMPLETE,
        expected_files=_REVIEWED_FILES_V2,
        reviewed_files=_REVIEWED_FILES_V2,
        partially_reviewed_files=(),
        missing_files=(),
        must_review_files=(),
        missing_must_review_files=(),
        degradation_causes=(),
    )

    assert coverage.status is CoverageStateV2.COMPLETE
    for invisible_path in _UNREVIEWABLE_CHANGED_FILES_V2:
        assert invisible_path not in coverage.expected_files
        assert invisible_path not in coverage.missing_files


@pytest.mark.parametrize("degradation_reason", list(CoverageDegradationReasonV2))
def test_no_coverage_degradation_reason_describes_unreviewable_material(
    degradation_reason: CoverageDegradationReasonV2,
) -> None:
    """Route (a): widen ``expected_files``, mark the rename degraded.

    The contract *accepts* this shape for every existing reason code -- so the
    obstacle is not structural, it is semantic. Each available reason asserts
    something untrue about a pure rename:

    ``artifact_missing``
        nothing is missing; the diff is complete and there is simply nothing
        to review. An operator would go looking for an absent artifact.
    ``budget_exhausted``
        no budget was consumed or exceeded.
    ``transport_failure`` / ``schema_failure``
        no transport or schema was involved; the path never reached one.
    ``model_uncertainty``
        no model ever saw it, so it expressed no uncertainty.

    Recording any of them would send a reader to diagnose a failure that did
    not happen. That is semantic distortion, which the grant forbids.
    """
    accepted = ChunkCoverageV2(
        status=CoverageStateV2.DEGRADED,
        expected_files=_REVIEWED_FILES_V2 + _UNREVIEWABLE_CHANGED_FILES_V2,
        reviewed_files=_REVIEWED_FILES_V2,
        partially_reviewed_files=(),
        missing_files=_UNREVIEWABLE_CHANGED_FILES_V2,
        must_review_files=(),
        missing_must_review_files=(),
        degradation_causes=(
            CoverageDegradationV2(
                reason_code=degradation_reason,
                affected_files=_UNREVIEWABLE_CHANGED_FILES_V2,
                detail="path carries no reviewable material",
            ),
        ),
    )

    assert accepted.status is CoverageStateV2.DEGRADED

    # The previous revision asserted `degradation_reason not in {}` -- an
    # empty **dict** literal, true for every possible value, in a test whose
    # stated purpose is to catch exactly this kind of empty claim. It is
    # replaced by a check with content: the vocabulary is pinned by name, so
    # the day an honest member is added this fails and the §8 STOP can be
    # re-derived rather than assumed to still hold.
    assert {member.value for member in CoverageDegradationReasonV2} == {
        "artifact_missing",
        "budget_exhausted",
        "transport_failure",
        "schema_failure",
        "model_uncertainty",
    }, (
        "the coverage degradation vocabulary changed; re-run the §8 "
        "determination before relying on STOP_SCOPE_CONTRACT_REQUIRED"
    )
    assert "unrepresentable_material" not in {
        member.value for member in CoverageDegradationReasonV2
    }


def test_partial_coverage_would_deny_the_distinction_the_grant_requires() -> None:
    """Route (b): report ``partial`` and file the rename under ``missing``.

    Structurally accepted, and semantically wrong in two ways at once. It
    asserts the *fragment* review was incomplete when it was complete, and it
    collapses ``DiffCoverage`` into ``ScopeCompleteness`` -- destroying exactly
    the distinction the grant requires be preserved. After this collapse no
    reader can tell "the model failed to review a reviewable file" from "this
    path had nothing to review", which are opposite operational situations.

    It is also unusable in practice: ``TargetPoliciesV2.allow_partial_coverage``
    is ``Literal[False]``, so every ordinary rename would become a policy
    violation.
    """
    partial = ChunkCoverageV2(
        status=CoverageStateV2.PARTIAL,
        expected_files=_REVIEWED_FILES_V2 + _UNREVIEWABLE_CHANGED_FILES_V2,
        reviewed_files=_REVIEWED_FILES_V2,
        partially_reviewed_files=(),
        missing_files=_UNREVIEWABLE_CHANGED_FILES_V2,
        must_review_files=(),
        missing_must_review_files=(),
        degradation_causes=(),
    )

    assert partial.status is CoverageStateV2.PARTIAL, (
        "the contract accepts the shape; the objection is semantic, not structural"
    )

    from app.agent_review.contracts_v2 import TargetPoliciesV2

    allow_partial = TargetPoliciesV2.model_fields["allow_partial_coverage"]
    assert "False" in str(allow_partial.annotation), (
        "partial coverage is forbidden by target policy, so route (b) would "
        "turn every pure rename into a policy violation"
    )


def test_no_readiness_reason_code_describes_incomplete_total_scope() -> None:
    """Route (c): say it in ``reason_codes``/``blockers``.

    The readiness vocabulary is fixed and published. None of its members means
    "some changed paths carry material this product cannot represent".
    ``coverage_failure`` is the nearest and is still false: coverage did not
    fail, it succeeded over the fragments it was defined on. ``policy_failure``
    is the next nearest and is also wrong -- no target policy was violated; the
    product simply could not render some material.

    The docstring previously said "walking it member by member", which
    overstated what the assertion does: the test pins the member set so the
    determination is re-run if the vocabulary ever changes. The member-by-member
    argument is in ADR-200F, where it belongs.
    """
    assert {reason.value for reason in ReadinessReasonV2} == {
        "schema_failure",
        "transport_failure",
        "coverage_failure",
        "policy_failure",
        "model_uncertainty",
        "finding_confirmation_required",
        "confirmed_code_finding",
        "head_mismatch",
        "identity_mismatch",
    }, "the readiness vocabulary changed; re-run the §8 determination"


def test_readiness_has_no_limitation_channel_to_carry_the_disposition() -> None:
    """Route (d): a ``limitations`` list, as ``TargetProfileV2`` has.

    ``ReviewReadinessV2`` has no such field, and adding one is a change to a
    *published* schema -- which the grant forbids doing silently. Hence the
    ADR and the versioned, additive proposal.
    """
    assert "limitations" not in ReviewReadinessV2.model_fields
    assert "scope" not in ReviewReadinessV2.model_fields
    assert "scope_completeness" not in ReviewReadinessV2.model_fields

    from app.agent_review.contracts_v2 import TargetProfileV2

    assert "limitations" in TargetProfileV2.model_fields, (
        "the concept exists elsewhere in v2, which is what makes its absence "
        "here a gap rather than a deliberate exclusion"
    )


def test_the_published_readiness_schema_is_frozen_material() -> None:
    """Establishes that this is a published-contract question, not a local one.

    If the schema were unpublished, authority C could simply add a field and
    move on. It is published, so the change is additive-and-versioned or it is
    nothing.
    """
    import pathlib

    schema_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "schemas"
        / "agent-review"
        / "v2"
        / "agent-review.review-readiness.v2.schema.json"
    )
    assert schema_path.is_file(), schema_path

    import json

    published = json.loads(schema_path.read_text(encoding="utf-8"))
    properties = published.get("properties", {})
    assert "limitations" not in properties
    assert "scope_completeness" not in properties


def test_spike_verdict_is_stop_scope_contract_required() -> None:
    """The determination itself, recorded as an assertion.

    Every route above is either semantically false or a silent change to a
    published schema. The grant's instruction for that outcome is explicit, so
    the verdict is ``STOP_SCOPE_CONTRACT_REQUIRED`` and the ADR is the
    deliverable.

    This is *not* a blocker on the rest of `#200-F`: the internal
    ``ScopeAssessmentV2`` authority is private, needs no published vocabulary,
    and still prevents a false ``ready``. What is blocked is *emitting* the
    distinction in the readiness artifact.
    """
    import pathlib

    adr_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs"
        / "adr"
        / "ADR-200F-SCOPE-COMPLETENESS-CONTRACT.md"
    )
    assert adr_path.is_file(), (
        "the §8 verdict is STOP_SCOPE_CONTRACT_REQUIRED, which obliges an ADR "
        f"for the smallest additive/versioned contract at {adr_path}"
    )

    adr_text = adr_path.read_text(encoding="utf-8")
    assert "STOP_SCOPE_CONTRACT_REQUIRED" in adr_text
    assert "additive" in adr_text
