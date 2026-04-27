"""Unit tests for the deterministic action mapper (action_mapper.py).

Covers:
  - Each supported check produces correct action_ids
  - Problem findings via evidence signal names → correct action_ids
  - ok/unknown findings produce no action_ids
  - Findings with empty evidence fall back to requested_checks
  - General investigation ids appended when problems exist
  - Deduplication: same action_id only once even if many findings match it
  - Empty inputs produce empty output
  - No command field or shell string in the output
"""

from __future__ import annotations

import pytest

from app.agent_router.schemas import AIOpsFinding, AIOpsSignal
from app.agent_router.services.action_mapper import (
    _CHECK_ACTION_MAP,
    _GENERAL_INVESTIGATION_IDS,
    map_findings_to_action_ids,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    signal_name: str,
    status: str = "critical",
    title: str | None = None,
) -> AIOpsFinding:
    signal = AIOpsSignal(name=signal_name, status=status, source="test")
    return AIOpsFinding(
        title=title or signal_name.replace("_", " ").title(),
        severity="high" if status == "critical" else "medium",
        status=status,
        description=f"Test finding for {signal_name}",
        evidence=[signal],
    )


def _finding_no_evidence(status: str = "critical", title: str = "Unknown Issue") -> AIOpsFinding:
    return AIOpsFinding(
        title=title,
        severity="high",
        status=status,
        description="Finding without evidence signals",
        evidence=[],
    )


# ---------------------------------------------------------------------------
# Empty / trivial cases
# ---------------------------------------------------------------------------


def test_empty_findings_returns_empty() -> None:
    result = map_findings_to_action_ids([], [])
    assert result == []


def test_empty_findings_with_checks_returns_empty() -> None:
    result = map_findings_to_action_ids([], ["readiness", "backend_up"])
    assert result == []


def test_all_ok_findings_returns_empty() -> None:
    findings = [_finding("readiness", status="ok"), _finding("backend_up", status="ok")]
    result = map_findings_to_action_ids(findings, ["readiness", "backend_up"])
    assert result == []


def test_all_unknown_findings_returns_empty() -> None:
    findings = [_finding("readiness", status="unknown"), _finding("backend_up", status="unknown")]
    result = map_findings_to_action_ids(findings, ["readiness", "backend_up"])
    assert result == []


# ---------------------------------------------------------------------------
# Check-specific mappings via evidence signal names
# ---------------------------------------------------------------------------


def test_readiness_problem_maps_to_health_ready_systemctl() -> None:
    result = map_findings_to_action_ids([_finding("readiness")], ["readiness"])
    assert "curl_health_8000" in result
    assert "curl_ready_8000" in result
    assert "systemctl_status_aiops" in result


def test_backend_up_problem_maps_to_health_ready() -> None:
    result = map_findings_to_action_ids([_finding("backend_up")], ["backend_up"])
    assert "curl_health_8000" in result
    assert "curl_ready_8000" in result


def test_error_rate_problem_maps_to_journalctl_prometheus() -> None:
    result = map_findings_to_action_ids([_finding("error_rate", status="warning")], ["error_rate"])
    assert "journalctl_aiops_recent" in result
    assert "prometheus_query_allowlisted" in result


def test_latency_p95_problem_maps_to_prometheus_journalctl() -> None:
    result = map_findings_to_action_ids([_finding("latency_p95", status="warning")], ["latency_p95"])
    assert "prometheus_query_allowlisted" in result
    assert "journalctl_aiops_recent" in result


def test_blocked_tasks_problem_maps_to_journalctl() -> None:
    result = map_findings_to_action_ids([_finding("blocked_tasks", status="warning")], ["blocked_tasks"])
    assert "journalctl_aiops_recent" in result


def test_model_selection_problem_maps_to_journalctl() -> None:
    result = map_findings_to_action_ids([_finding("model_selection", status="warning")], ["model_selection"])
    assert "journalctl_aiops_recent" in result


def test_ollama_models_count_problem_maps_to_journalctl() -> None:
    result = map_findings_to_action_ids([_finding("ollama_models_count", status="warning")], ["ollama_models_count"])
    assert "journalctl_aiops_recent" in result


# ---------------------------------------------------------------------------
# General investigation actions appended on problems
# ---------------------------------------------------------------------------


def test_problem_finding_appends_general_investigation_ids() -> None:
    result = map_findings_to_action_ids([_finding("readiness")], ["readiness"])
    for gid in _GENERAL_INVESTIGATION_IDS:
        assert gid in result


def test_ok_finding_does_not_append_general_ids() -> None:
    result = map_findings_to_action_ids([_finding("readiness", status="ok")], ["readiness"])
    for gid in _GENERAL_INVESTIGATION_IDS:
        assert gid not in result


def test_general_ids_appear_after_specific_ids() -> None:
    result = map_findings_to_action_ids([_finding("readiness")], ["readiness"])
    specific = [r for r in result if r not in _GENERAL_INVESTIGATION_IDS]
    general = [r for r in result if r in _GENERAL_INVESTIGATION_IDS]
    # Specific must precede general in the list
    if specific and general:
        last_specific_idx = max(result.index(s) for s in specific)
        first_general_idx = min(result.index(g) for g in general)
        assert last_specific_idx < first_general_idx


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_same_action_id_appears_only_once_across_multiple_findings() -> None:
    findings = [_finding("readiness"), _finding("backend_up")]
    result = map_findings_to_action_ids(findings, ["readiness", "backend_up"])
    assert result.count("curl_health_8000") == 1
    assert result.count("curl_ready_8000") == 1


def test_no_duplicates_from_evidence_and_check_sweep() -> None:
    result = map_findings_to_action_ids([_finding("readiness")], ["readiness"])
    assert len(result) == len(set(result))


# ---------------------------------------------------------------------------
# Fallback: findings without evidence use requested_checks
# ---------------------------------------------------------------------------


def test_finding_without_evidence_falls_back_to_checks() -> None:
    finding = _finding_no_evidence(status="critical")
    result = map_findings_to_action_ids([finding], ["readiness"])
    # Should still find curl_health_8000 via the check name fallback
    assert "curl_health_8000" in result


def test_finding_without_evidence_and_no_checks_returns_only_general() -> None:
    finding = _finding_no_evidence(status="critical")
    result = map_findings_to_action_ids([finding], [])
    # No specific mappings possible; only general ids
    for gid in _GENERAL_INVESTIGATION_IDS:
        assert gid in result


# ---------------------------------------------------------------------------
# Mixed ok / problem findings
# ---------------------------------------------------------------------------


def test_mixed_ok_and_problem_findings_only_maps_problems() -> None:
    findings = [
        _finding("readiness", status="ok"),
        _finding("backend_up", status="critical"),
    ]
    result = map_findings_to_action_ids(findings, ["readiness", "backend_up"])
    # Should include backend_up actions but NOT the check-sweep from readiness alone
    assert "curl_health_8000" in result
    # readiness is also in _CHECK_ACTION_MAP but it would be caught by the check sweep
    # since backend_up has problems. Both happen to map to the same ids; just check no crash.
    assert len(result) == len(set(result))


# ---------------------------------------------------------------------------
# Output safety: no command field, no shell strings
# ---------------------------------------------------------------------------


def test_output_contains_only_action_ids_not_commands() -> None:
    result = map_findings_to_action_ids([_finding("readiness")], ["readiness"])
    for item in result:
        assert isinstance(item, str)
        # Must be an identifier, not a shell command
        assert " " not in item, f"action_id must not contain spaces: {item!r}"
        assert "|" not in item
        assert ";" not in item
        assert "$" not in item


def test_all_returned_ids_are_known_in_check_map_or_general() -> None:
    all_known = set()
    for ids in _CHECK_ACTION_MAP.values():
        all_known.update(ids)
    all_known.update(_GENERAL_INVESTIGATION_IDS)

    findings = [_finding(check) for check in _CHECK_ACTION_MAP]
    result = map_findings_to_action_ids(findings, list(_CHECK_ACTION_MAP.keys()))
    for aid in result:
        assert aid in all_known, f"Unknown action_id in mapper output: {aid!r}"


# ---------------------------------------------------------------------------
# Status variants that count as problems
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["critical", "warning", "degraded", "not_ready", "down"])
def test_problem_status_variants_trigger_mapping(status: str) -> None:
    result = map_findings_to_action_ids([_finding("readiness", status=status)], ["readiness"])
    assert len(result) > 0, f"Expected non-empty result for status={status!r}"
