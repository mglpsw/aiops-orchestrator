from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.agent_review_v2.harness import (
    CaseFileV2,
    CaseHunkV2,
    EvalCaseV2,
    ExpectedFindingV2,
    InjectedFindingV2,
    compute_eval_summary_v2,
    run_eval_case_v2,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "agent_review" / "fixtures" / "v2"
_HEAD_SHA = "2" * 40


def _hunk(**overrides: object) -> dict:
    base = {"old_start": 10, "old_lines": 6, "new_start": 10, "new_lines": 8, "seed": "test-hunk"}
    base.update(overrides)
    return base


def _base_case(**overrides: object) -> dict:
    case: dict = {
        "case_id": "unit-test-case",
        "category": "contract",
        "target": "agent_escala",
        "files": [{"path": "backend/scheduling/shift_rules.py", "hunks": [_hunk()]}],
        "expected_readiness": "ready",
        "rationale": "unit test fixture",
    }
    case.update(overrides)
    return case


def test_eval_case_rejects_unknown_field():
    with pytest.raises(ValidationError):
        EvalCaseV2.model_validate({**_base_case(), "unexpected_field": True})


def test_eval_case_rejects_missing_rationale():
    raw = _base_case()
    del raw["rationale"]
    with pytest.raises(ValidationError):
        EvalCaseV2.model_validate(raw)


def test_ready_case_with_no_findings():
    case = EvalCaseV2.model_validate(_base_case())
    result = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert result.actual_readiness == "ready"
    assert result.readiness_matches
    assert result.expected_findings_recovered == 0
    assert result.forbidden_findings_leaked == 0
    assert not result.blocked_at_assembly
    assert result.manifest_hash is not None


def test_binary_required_path_blocks_at_assembly():
    case = EvalCaseV2.model_validate(
        _base_case(
            files=[{"path": "backend/scheduling/shift_rules.py", "is_binary": True}],
            expected_readiness="blocked_pipeline",
        )
    )
    result = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert result.blocked_at_assembly
    assert result.actual_readiness == "blocked_pipeline"
    assert result.readiness_matches
    assert result.manifest_hash is None
    assert result.blocked_reason_code == "run_assembly_required_path_unrepresentable"


def test_new_finding_resolves_manual_required_and_is_recovered():
    case = EvalCaseV2.model_validate(
        _base_case(
            injected_findings=[
                {"file_path": "backend/scheduling/shift_rules.py", "severity": "P2", "line_start": 12, "line_end": 13}
            ],
            expected_readiness="manual_required",
            expected_findings=[
                {
                    "severity": "P2",
                    "file_path": "backend/scheduling/shift_rules.py",
                    "line_start": 12,
                    "line_end": 13,
                    "invariant": "test invariant",
                    "root_cause": "test root cause",
                }
            ],
        )
    )
    result = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert result.actual_readiness == "manual_required"
    assert result.readiness_matches
    assert result.expected_findings_recovered == 1
    assert result.expected_findings_total == 1


def test_confirmed_finding_resolves_blocked_code():
    case = EvalCaseV2.model_validate(
        _base_case(
            confirmed_findings=[
                {"file_path": "backend/scheduling/shift_rules.py", "severity": "P1", "line_start": 12, "line_end": 13}
            ],
            expected_readiness="blocked_code",
            expected_findings=[
                {
                    "severity": "P1",
                    "file_path": "backend/scheduling/shift_rules.py",
                    "line_start": 12,
                    "line_end": 13,
                    "invariant": "test invariant",
                    "root_cause": "test root cause",
                }
            ],
        )
    )
    result = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert result.actual_readiness == "blocked_code"
    assert result.readiness_matches
    assert result.expected_findings_recovered == 1


def test_confirmed_finding_overlapping_injected_finding_at_same_location():
    """Regression: `lifecycle_v2._dedup_key` deliberately excludes
    severity from its dedup key (the same defect can legitimately be
    re-observed at a different severity across rounds), so an
    `injected_findings` entry and a `confirmed_findings` entry at the SAME
    (file_path, line_start, line_end) but DIFFERENT severity collapse into
    ONE synthesized record with two provenance entries. An earlier harness
    revision picked a single, arbitrary provenance key when deciding what
    to confirm and silently missed the confirmation in exactly this case
    (reproduced directly: it resolved to `manual_required` instead of
    `blocked_code`). Fixed by checking ALL of a record's provenance keys
    against `confirmed_keys`, never just one."""

    case = EvalCaseV2.model_validate(
        _base_case(
            injected_findings=[
                {"file_path": "backend/scheduling/shift_rules.py", "severity": "P2", "line_start": 12, "line_end": 13}
            ],
            confirmed_findings=[
                {"file_path": "backend/scheduling/shift_rules.py", "severity": "P1", "line_start": 12, "line_end": 13}
            ],
            expected_readiness="blocked_code",
        )
    )
    result = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert result.actual_readiness == "blocked_code"
    assert result.readiness_matches


def test_forbidden_finding_leak_is_detected_when_actually_injected():
    """Non-vacuity proof: forbidden_findings_leaked must be 0 when the
    forbidden finding was never injected, and >0 when it was -- this test
    exercises both branches directly, not merely trusting the field exists."""

    not_leaked_case = EvalCaseV2.model_validate(
        _base_case(
            forbidden_findings=[
                {
                    "severity": "P1",
                    "file_path": "backend/scheduling/shift_rules.py",
                    "line_start": 99,
                    "line_end": 99,
                    "invariant": "should never appear",
                    "root_cause": "never injected",
                }
            ],
        )
    )
    clean_result = run_eval_case_v2(not_leaked_case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert clean_result.forbidden_findings_leaked == 0

    leaked_case = EvalCaseV2.model_validate(
        _base_case(
            injected_findings=[
                {"file_path": "backend/scheduling/shift_rules.py", "severity": "P2", "line_start": 12, "line_end": 13}
            ],
            expected_readiness="manual_required",
            forbidden_findings=[
                {
                    "severity": "P2",
                    "file_path": "backend/scheduling/shift_rules.py",
                    "line_start": 12,
                    "line_end": 13,
                    "invariant": "deliberately matches the injected finding",
                    "root_cause": "mutation-style unit test",
                }
            ],
        )
    )
    leaked_result = run_eval_case_v2(leaked_case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert leaked_result.forbidden_findings_leaked == 1


def test_stale_reason_codes_short_circuit_to_stale():
    case = EvalCaseV2.model_validate(_base_case(stale_reason_codes=["head_mismatch"], expected_readiness="stale"))
    result = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert result.actual_readiness == "stale"
    assert result.readiness_matches


def test_byte_stability_across_two_independent_runs():
    case = EvalCaseV2.model_validate(_base_case())
    result_a = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    result_b = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    assert result_a.manifest_hash == result_b.manifest_hash
    assert result_a.payload_hashes == result_b.payload_hashes


def test_compute_eval_summary_flags_false_approval():
    case = EvalCaseV2.model_validate(_base_case())
    result = run_eval_case_v2(case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    # Force a false-approval scenario: expected non-ready, actual ready.
    mutated = result.__class__(
        **{**result.__dict__, "expected_readiness": "blocked_pipeline", "readiness_matches": False}
    )
    summary = compute_eval_summary_v2([mutated])
    assert mutated.case_id in summary.false_approvals
    assert summary.readiness_mismatches == (mutated.case_id,)


def test_compute_eval_summary_counts_stale_correctness():
    stale_case = EvalCaseV2.model_validate(
        _base_case(stale_reason_codes=["head_mismatch"], expected_readiness="stale")
    )
    stale_result = run_eval_case_v2(stale_case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)
    ready_case = EvalCaseV2.model_validate(_base_case())
    ready_result = run_eval_case_v2(ready_case, fixtures_root=FIXTURES_ROOT, head_sha=_HEAD_SHA)

    summary = compute_eval_summary_v2([stale_result, ready_result])
    assert summary.stale_cases_total == 1
    assert summary.stale_cases_correct == 1
    assert summary.false_approvals == ()
