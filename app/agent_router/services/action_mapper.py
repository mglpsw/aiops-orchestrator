"""Deterministic mapper from diagnostic findings to action_ids.

Maps AIOpsFinding objects (produced by the Diagnostic Engine) to action_ids
that exist in the Action Catalog. No LLM, no free-text commands, no shell.

The mapping table is the only place that links check names / signal names to
catalog action_ids. Update the table here when new actions are added to the
catalog; never generate action_ids programmatically or via LLM.

Design constraints:
  - Input: list[AIOpsFinding] + requested check names
  - Output: deduplicated list[str] of action_ids (may be empty)
  - Only action_ids that exist in config/actions.yaml are referenced here;
    unknown ones will be caught by the planner's policy gate (fail-closed).
  - "ok" and "unknown" findings do not trigger action suggestions.
  - action_ids are ordered: specific (per-check) first, general last.
"""

from __future__ import annotations

from app.agent_router.schemas import AIOpsFinding

# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

# Maps signal/check names to action_ids to suggest when that check has a problem.
# Order within each list matters: the planner preserves it.
_CHECK_ACTION_MAP: dict[str, list[str]] = {
    "readiness": ["curl_health_8000", "curl_ready_8000", "systemctl_status_aiops"],
    "readiness_status": ["curl_health_8000", "curl_ready_8000", "systemctl_status_aiops"],
    "backend_up": ["curl_health_8000", "curl_ready_8000"],
    "error_rate": ["journalctl_aiops_recent", "prometheus_query"],
    "error_rate_high": ["journalctl_aiops_recent", "prometheus_query"],
    "latency_p95": ["prometheus_query", "journalctl_aiops_recent"],
    "latency_p95_high": ["prometheus_query", "journalctl_aiops_recent"],
    "blocked_tasks": ["journalctl_aiops_recent"],
    "route_block_spike": ["journalctl_aiops_recent"],
    "rate_limit_spike": ["prometheus_query", "journalctl_aiops_recent"],
    "prometheus_scrape_staleness": ["prometheus_query"],
    "aiops_catalog_not_ready": ["git_status", "git_diff_stat"],
    "model_selection": ["journalctl_aiops_recent"],
    "ollama_models_count": ["journalctl_aiops_recent"],
}

# Added once when any problem finding exists, regardless of type.
_GENERAL_INVESTIGATION_IDS: list[str] = ["git_status", "git_log_recent"]

# Statuses that indicate a real problem (trigger action suggestions).
_PROBLEM_STATUSES: frozenset[str] = frozenset({"critical", "warning", "degraded", "not_ready", "down"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def map_findings_to_action_ids(
    findings: list[AIOpsFinding],
    requested_checks: list[str],
) -> list[str]:
    """Return a deduplicated, ordered list of action_ids for the given findings.

    Only findings with a problem status contribute to the result.
    If no findings have problems, returns an empty list (no plan needed).
    """
    seen: set[str] = set()
    result: list[str] = []
    has_problems = False

    def _add(ids: list[str]) -> None:
        for aid in ids:
            if aid not in seen:
                seen.add(aid)
                result.append(aid)

    # 1. Map problem findings via their evidence signal names (most precise).
    for finding in findings:
        if finding.status.lower() not in _PROBLEM_STATUSES:
            continue
        has_problems = True
        for signal in finding.evidence:
            mapped = _CHECK_ACTION_MAP.get(signal.name.lower())
            if mapped:
                _add(mapped)

    # 2. If problems exist, also sweep requested check names as a fallback
    #    (covers findings whose evidence list was empty).
    if has_problems:
        for check in requested_checks:
            mapped = _CHECK_ACTION_MAP.get(check.lower())
            if mapped:
                _add(mapped)

    # 3. General investigation actions appended last for any non-ok diagnosis.
    if has_problems:
        _add(_GENERAL_INVESTIGATION_IDS)

    return result


def recommended_action_ids_for_check(check_name: str) -> list[str]:
    """Return the deterministic allowlisted action_ids for one check name."""
    return list(_CHECK_ACTION_MAP.get(check_name.lower(), []))
