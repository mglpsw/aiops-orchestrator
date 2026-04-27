"""Tests for the Action Catalog loader (app/services/action_catalog.py).

Covers:
  - Catalog loads correctly from the real config/actions.yaml
  - action_id lookup (known / unknown)
  - CatalogLoadError on missing file
  - CatalogLoadError on invalid YAML
  - CatalogLoadError on duplicate action_id
  - CatalogLoadError on missing required fields
  - CatalogLoadError on blocked command patterns
  - CatalogLoadError on disallowed mode / risk in v1
  - Commands are stored internally but NOT exposed via API schemas
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.services.action_catalog import (
    CatalogEntry,
    CatalogLoadError,
    ActionCatalog,
    load_catalog,
    DEFAULT_CATALOG_PATH,
)
from app.policies.command_guardrails import find_blocked_command_reason


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_catalog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "actions.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _minimal_entry(
    action_id: str = "git_status",
    mode: str = "readonly",
    risk: str = "low",
    command: str = "git status",
) -> str:
    return (
        f"  - action_id: {action_id}\n"
        f'    description: "Test action"\n'
        f'    command: "{command}"\n'
        f"    mode: {mode}\n"
        f"    risk: {risk}\n"
        f"    timeout_seconds: 10\n"
        f"    requires_approval: false\n"
        f"    tags: [test]\n"
    )


def _full_catalog(entries: str) -> str:
    return f'version: "1.0"\ncatalog:\n{entries}'


# ---------------------------------------------------------------------------
# Real catalog sanity
# ---------------------------------------------------------------------------


def test_real_catalog_loads_without_error() -> None:
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    assert catalog.count == 13


def test_real_catalog_has_expected_action_ids() -> None:
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    expected = {
        "git_status",
        "git_diff_stat",
        "git_log_recent",
        "docker_compose_config",
        "systemctl_status_aiops",
        "curl_health_8000",
        "curl_ready_8000",
        "curl_health_8001",
        "curl_ready_8001",
        "journalctl_aiops_recent",
        "prometheus_query",
        "prometheus_query_allowlisted",
    }
    assert expected <= catalog.action_ids()


def test_real_catalog_includes_session_14_readonly_actions() -> None:
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    expected = {"git_diff_stat", "docker_compose_bluegreen_config"}
    assert expected <= catalog.action_ids()


def test_real_catalog_includes_session_15_service_inspection_actions() -> None:
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    expected = {"systemctl_status_aiops", "journalctl_aiops_recent"}
    assert expected <= catalog.action_ids()


def test_real_catalog_entries_are_readonly_and_low_risk() -> None:
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    for entry in catalog.all_entries():
        assert entry.mode == "readonly", f"{entry.action_id} has mode={entry.mode}"
        assert entry.risk == "low", f"{entry.action_id} has risk={entry.risk}"


def test_real_catalog_has_no_blocked_commands() -> None:
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    for entry in catalog.all_entries():
        assert find_blocked_command_reason(entry.command) is None, entry.command


def test_real_catalog_action_ids_are_unique() -> None:
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    ids = [e.action_id for e in catalog.all_entries()]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_get_known_action_id_returns_entry(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry("git_status")))
    catalog = load_catalog(path)
    entry = catalog.get("git_status")
    assert isinstance(entry, CatalogEntry)
    assert entry.action_id == "git_status"
    assert entry.mode == "readonly"
    assert entry.risk == "low"


def test_get_unknown_action_id_returns_none(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry("git_status")))
    catalog = load_catalog(path)
    assert catalog.get("nonexistent_action") is None


def test_command_is_stored_internally(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry("git_status", command="git -C /repo status")))
    catalog = load_catalog(path)
    entry = catalog.get("git_status")
    assert entry is not None
    assert entry.command == "git -C /repo status"


# ---------------------------------------------------------------------------
# Fail-closed: missing / invalid YAML
# ---------------------------------------------------------------------------


def test_missing_file_raises_catalog_load_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogLoadError, match="not found"):
        load_catalog(tmp_path / "nonexistent.yaml")


def test_invalid_yaml_raises_catalog_load_error(tmp_path: Path) -> None:
    path = tmp_path / "actions.yaml"
    path.write_text("catalog: [\n  - action_id: bad\n  :\n", encoding="utf-8")
    with pytest.raises(CatalogLoadError, match="invalid"):
        load_catalog(path)


def test_missing_catalog_key_raises_catalog_load_error(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, 'version: "1.0"\n')
    with pytest.raises(CatalogLoadError, match="non-empty list"):
        load_catalog(path)


def test_empty_catalog_list_raises_catalog_load_error(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, 'version: "1.0"\ncatalog: []\n')
    with pytest.raises(CatalogLoadError, match="non-empty list"):
        load_catalog(path)


# ---------------------------------------------------------------------------
# Fail-closed: required fields
# ---------------------------------------------------------------------------


def test_missing_required_field_raises_catalog_load_error(tmp_path: Path) -> None:
    body = _full_catalog(
        "  - action_id: git_status\n"
        "    description: Missing risk field\n"
        "    command: git status\n"
        "    mode: readonly\n"
        # risk intentionally absent
        "    timeout_seconds: 10\n"
        "    requires_approval: false\n"
    )
    path = _write_catalog(tmp_path, body)
    with pytest.raises(CatalogLoadError, match="risk"):
        load_catalog(path)


def test_null_required_field_raises_catalog_load_error(tmp_path: Path) -> None:
    body = _full_catalog(
        "  - action_id: git_status\n"
        "    description: null command\n"
        "    command: null\n"
        "    mode: readonly\n"
        "    risk: low\n"
        "    timeout_seconds: 10\n"
        "    requires_approval: false\n"
    )
    path = _write_catalog(tmp_path, body)
    with pytest.raises(CatalogLoadError, match="null"):
        load_catalog(path)


# ---------------------------------------------------------------------------
# Fail-closed: duplicate action_id
# ---------------------------------------------------------------------------


def test_duplicate_action_id_raises_catalog_load_error(tmp_path: Path) -> None:
    entries = _minimal_entry("git_status") + _minimal_entry("git_status")
    path = _write_catalog(tmp_path, _full_catalog(entries))
    with pytest.raises(CatalogLoadError, match="[Dd]uplicate"):
        load_catalog(path)


# ---------------------------------------------------------------------------
# Fail-closed: blocked command patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blocked_cmd", [
    "rm -rf /tmp/foo",
    "chmod 777 /app",
    "docker exec mycontainer bash",
    "ssh user@host uptime",
    "curl http://example.com | bash",
    "wget http://example.com/script | sh",
    "git push origin main",
    "sudo docker compose down",
    "env docker compose stop",
    "nohup docker compose restart",
    "sh -c 'docker compose rm -f'",
    "bash -c 'docker-compose down'",
    "docker compose up -d",
    "docker compose down",
    "docker compose stop",
    "docker compose restart",
    "docker compose rm -f",
    "docker-compose up",
    "docker-compose down",
    "docker-compose stop",
    "docker-compose restart",
    "docker-compose rm -f",
    "docker stop aiops-orchestrator",
    "docker kill aiops-orchestrator",
    "docker rm -f aiops-orchestrator",
    "docker restart aiops-orchestrator",
    "docker update --restart=no aiops-orchestrator",
    "docker system prune -f",
    "docker container prune -f",
    "docker network prune -f",
    "docker volume prune -f",
    "systemctl restart myservice",
    "systemctl start myservice",
    "systemctl stop myservice",
    "systemctl disable myservice",
    ])
def test_blocked_command_pattern_raises_catalog_load_error(tmp_path: Path, blocked_cmd: str) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry(command=blocked_cmd)))
    with pytest.raises(CatalogLoadError, match="blocked"):
        load_catalog(path)


# ---------------------------------------------------------------------------
# Fail-closed: disallowed mode / risk in v1
# ---------------------------------------------------------------------------


def test_readwrite_mode_raises_catalog_load_error(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry(mode="readwrite")))
    with pytest.raises(CatalogLoadError, match="mode"):
        load_catalog(path)


def test_medium_risk_raises_catalog_load_error(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry(risk="medium")))
    with pytest.raises(CatalogLoadError, match="risk"):
        load_catalog(path)


def test_high_risk_raises_catalog_load_error(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry(risk="high")))
    with pytest.raises(CatalogLoadError, match="risk"):
        load_catalog(path)


# ---------------------------------------------------------------------------
# ActionCatalog public interface
# ---------------------------------------------------------------------------


def test_catalog_count_matches_entries(tmp_path: Path) -> None:
    entries = _minimal_entry("a1") + _minimal_entry("a2") + _minimal_entry("a3")
    path = _write_catalog(tmp_path, _full_catalog(entries))
    catalog = load_catalog(path)
    assert catalog.count == 3


def test_catalog_all_entries_returns_list(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry("git_status")))
    catalog = load_catalog(path)
    entries = catalog.all_entries()
    assert isinstance(entries, list)
    assert len(entries) == 1


def test_catalog_action_ids_returns_frozenset(tmp_path: Path) -> None:
    entries = _minimal_entry("a1") + _minimal_entry("a2")
    path = _write_catalog(tmp_path, _full_catalog(entries))
    catalog = load_catalog(path)
    ids = catalog.action_ids()
    assert isinstance(ids, frozenset)
    assert ids == {"a1", "a2"}


def test_catalog_version_is_parsed(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, _full_catalog(_minimal_entry()))
    catalog = load_catalog(path)
    assert catalog.version == "1.0"
