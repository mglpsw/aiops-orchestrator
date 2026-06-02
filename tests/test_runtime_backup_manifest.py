from __future__ import annotations

import importlib.util
import json
import py_compile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCRIPT = ROOT / "scripts" / "aiops-runtime-backup-manifest.py"
FAKE_SECRET = "RUNTIME_BACKUP_MANIFEST_FAKE_SECRET"


def _load_script(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"test_{path.stem.replace('-', '_')}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_repo(
    tmp_path: Path,
    *,
    with_config: bool = True,
    with_audit: bool = True,
    with_approvals: bool = True,
    with_runs: bool = True,
    with_data: bool = True,
    with_compose: bool = True,
    with_env: bool = True,
) -> Path:
    repo_root = tmp_path / "aiops-orchestrator"
    repo_root.mkdir()

    # Fake git HEAD + ref (read without subprocess).
    (repo_root / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo_root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo_root / ".git" / "refs" / "heads" / "main").write_text(
        "1234567890abcdef1234567890abcdef12345678\n",
        encoding="utf-8",
    )

    if with_env:
        (repo_root / ".env").write_text(f"AIOPS_FAKE_SECRET={FAKE_SECRET}\n", encoding="utf-8")
    if with_config:
        (repo_root / "config").mkdir()
        (repo_root / "config" / "actions.yaml").write_text("actions: []\n", encoding="utf-8")
    if with_audit:
        (repo_root / "var" / "audit").mkdir(parents=True)
    if with_approvals:
        (repo_root / "var" / "approvals").mkdir(parents=True)
    if with_runs:
        (repo_root / "var" / "runs").mkdir(parents=True)
    if with_data:
        (repo_root / "data").mkdir()
    if with_compose:
        (repo_root / "deploy").mkdir()
        (repo_root / "deploy" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    return repo_root


def _store(payload: dict[str, Any], store_id: str) -> dict[str, Any]:
    matches = [store for store in payload["stores"] if store["id"] == store_id]
    assert len(matches) == 1, f"expected exactly one store {store_id!r}"
    return matches[0]


def test_rejects_output_inside_repo_root(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = repo_root / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 1
    assert not output.exists()


def test_does_not_read_env_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "manifest.json"
    original_read_text = Path.read_text

    def guarded_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == ".env":
            raise AssertionError(".env must not be read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    assert FAKE_SECRET not in output.read_text(encoding="utf-8")


def test_redacts_absolute_repo_path(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    output_text = output.read_text(encoding="utf-8")
    assert str(repo_root.resolve()) not in output_text
    assert json.loads(output_text)["repo_root"] == repo_root.name


def test_reads_fake_git_head_and_ref_without_subprocess(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["baseline_commit"] == "1234567890abcdef1234567890abcdef12345678"
    assert not hasattr(module, "subprocess")


def test_reports_config_as_required_store(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    config = _store(payload, "config")
    assert config["exists"] is True
    assert config["backup_required"] is True
    assert config["rollback_required"] is True
    assert config["baseline_state"] == "present"


def test_reports_audit_as_required_store(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    audit = _store(payload, "audit")
    assert audit["exists"] is True
    assert audit["backup_required"] is True
    assert audit["rollback_required"] is True
    assert audit["baseline_state"] == "present"


def test_reports_approvals_missing_baseline(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path, with_approvals=False)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    approvals = _store(payload, "approvals")
    assert approvals["exists"] is False
    assert approvals["kind"] == "missing_baseline"
    assert approvals["baseline_state"] == "missing"
    assert approvals["backup_required"] is True
    assert approvals["rollback_required"] is True
    assert "approvals_missing_baseline" in payload["limitations"]


def test_reports_runs_missing_baseline(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path, with_runs=False)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    runs = _store(payload, "runs")
    assert runs["exists"] is False
    assert runs["kind"] == "missing_baseline"
    assert runs["baseline_state"] == "missing"
    assert runs["backup_required"] is True
    assert runs["rollback_required"] is True
    assert "runs_missing_baseline" in payload["limitations"]


def test_documents_data_docker_volume_hint_when_host_data_missing(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path, with_data=False)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    data = _store(payload, "data")
    assert data["exists"] is False
    assert data["kind"] == "container_path"
    assert data["backup_required"] is True
    assert data["rollback_required"] is True
    hints = payload["docker_volume_hints"]
    assert hints == [
        {
            "id": "aiops-data",
            "container_path": "/app/data/aiops.db",
            "backup_required": True,
            "rollback_required": True,
        }
    ]


def test_minimum_backup_complete_false_when_approvals_or_runs_missing(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path, with_approvals=False, with_runs=False)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["minimum_backup_complete"] is False
    assert "approvals_missing_baseline" in payload["limitations"]
    assert "runs_missing_baseline" in payload["limitations"]


def test_minimum_backup_complete_true_when_all_host_stores_exist(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["minimum_backup_complete"] is True
    assert payload["limitations"] == []


def test_output_contains_no_fake_secret(tmp_path: Path) -> None:
    module = _load_script(MANIFEST_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "manifest.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    assert FAKE_SECRET not in output.read_text(encoding="utf-8")


def test_script_compiles() -> None:
    py_compile.compile(str(MANIFEST_SCRIPT), doraise=True)
