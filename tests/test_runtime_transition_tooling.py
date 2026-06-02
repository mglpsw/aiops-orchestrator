from __future__ import annotations

import importlib.util
import json
import py_compile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_SCRIPT = ROOT / "scripts" / "aiops-runtime-inventory.py"
POSTCHECK_SCRIPT = ROOT / "scripts" / "aiops-runtime-postcheck.py"
FAKE_SECRET = "RUNTIME_TRANSITION_FAKE_SECRET"


def _load_script(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"test_{path.stem.replace('-', '_')}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_repo(tmp_path: Path, *, version: str = "0.19.0", complete_paths: bool = True) -> Path:
    repo_root = tmp_path / "aiops-orchestrator"
    (repo_root / "app" / "core").mkdir(parents=True)
    (repo_root / "app" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (repo_root / "app" / "core" / "config.py").write_text(
        f'class Settings:\n    app_version: str = "{version}"\n',
        encoding="utf-8",
    )
    (repo_root / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo_root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo_root / ".git" / "refs" / "heads" / "main").write_text(
        "1234567890abcdef1234567890abcdef12345678\n",
        encoding="utf-8",
    )
    (repo_root / ".env").write_text(f"AIOPS_FAKE_SECRET={FAKE_SECRET}\n", encoding="utf-8")

    if complete_paths:
        (repo_root / "config").mkdir()
        (repo_root / "config" / "actions.yaml").write_text("actions: []\n", encoding="utf-8")
        (repo_root / "var" / "audit").mkdir(parents=True)
        (repo_root / "var" / "approvals").mkdir(parents=True)
        (repo_root / "var" / "runs").mkdir(parents=True)
        (repo_root / "data").mkdir()
        (repo_root / "deploy").mkdir()
        (repo_root / "deploy" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    return repo_root


class FakeResponse:
    status = 200

    def __init__(self, body: bytes = b'{"ok": true}') -> None:
        self.body = body
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self.body if size == -1 else self.body[:size]

    def close(self) -> None:
        self.closed = True


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, module: ModuleType, body: bytes = b'{"ok": true}') -> None:
    calls: list[str] = []

    def fake_urlopen(url: str, timeout: int = 0) -> FakeResponse:
        calls.append(url)
        assert timeout == 2
        return FakeResponse(body)

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    module._test_urlopen_calls = calls


def test_inventory_rejects_output_inside_repo_root(tmp_path: Path) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = repo_root / "inventory.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 1
    assert not output.exists()


def test_postcheck_rejects_output_inside_repo_root(tmp_path: Path) -> None:
    module = _load_script(POSTCHECK_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = repo_root / "postcheck.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 1
    assert not output.exists()


def test_inventory_does_not_read_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "inventory.json"
    original_read_text = Path.read_text

    def guarded_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == ".env":
            raise AssertionError(".env must not be read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    assert FAKE_SECRET not in output.read_text(encoding="utf-8")


def test_inventory_redacts_absolute_repo_path(tmp_path: Path) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "inventory.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    output_text = output.read_text(encoding="utf-8")
    assert str(repo_root.resolve()) not in output_text
    assert json.loads(output_text)["repo_root"] == repo_root.name


def test_inventory_reads_fake_git_head_and_ref_without_subprocess(tmp_path: Path) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "inventory.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["git"] == {
        "head_ref": "refs/heads/main",
        "head_sha": "1234567890abcdef1234567890abcdef12345678",
        "status": "observed",
    }
    assert not hasattr(module, "subprocess")


def test_inventory_detects_app_version_from_fake_files(tmp_path: Path) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    repo_root = _make_repo(tmp_path, version="0.18.7")
    output = tmp_path / "inventory.json"

    result = module.main(["--repo-root", str(repo_root), "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["version"]["app_init_version"] == "0.18.7"
    assert payload["version"]["settings_default_app_version"] == "0.18.7"
    assert payload["version"]["status"] == "observed"


def test_inventory_rejects_external_health_url(tmp_path: Path) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "inventory.json"

    result = module.main(
        [
            "--repo-root",
            str(repo_root),
            "--output",
            str(output),
            "--include-health-url",
            "https://example.com/health",
        ]
    )

    assert result == 1
    assert not output.exists()


def test_inventory_accepts_localhost_health_url_with_monkeypatched_urllib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    _patch_urlopen(monkeypatch, module, body=b'{"ok": true, "token": "RUNTIME_TRANSITION_FAKE_SECRET"}')
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "inventory.json"

    result = module.main(
        [
            "--repo-root",
            str(repo_root),
            "--output",
            str(output),
            "--include-health-url",
            "http://localhost:8000/health",
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["local_http_checks"]["health"]["status"] == "ok"
    assert payload["local_http_checks"]["health"]["http_status"] == 200
    assert "RUNTIME_TRANSITION_FAKE_SECRET" not in output.read_text(encoding="utf-8")
    assert module._test_urlopen_calls == ["http://localhost:8000/health"]


def test_postcheck_marks_ready_false_when_expected_version_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script(POSTCHECK_SCRIPT)
    _patch_urlopen(monkeypatch, module)
    repo_root = _make_repo(tmp_path, version="0.18.0")
    output = tmp_path / "postcheck.json"

    result = module.main(
        [
            "--repo-root",
            str(repo_root),
            "--output",
            str(output),
            "--expected-version",
            "0.19.0",
            "--health-url",
            "http://127.0.0.1:8000/health",
            "--ready-url",
            "http://127.0.0.1:8000/ready",
            "--metrics-url",
            "http://127.0.0.1:8000/metrics",
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ready_for_final_release"] is False
    assert "expected_version_mismatch" in payload["limitations"]


def test_postcheck_marks_ready_false_when_health_or_ready_are_skipped(tmp_path: Path) -> None:
    module = _load_script(POSTCHECK_SCRIPT)
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "postcheck.json"

    result = module.main(
        [
            "--repo-root",
            str(repo_root),
            "--output",
            str(output),
            "--expected-version",
            "0.19.0",
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["checks"]["health"] == "skipped"
    assert payload["checks"]["ready"] == "skipped"
    assert payload["checks"]["metrics"] == "skipped"
    assert payload["ready_for_final_release"] is False


def test_postcheck_marks_ready_true_only_when_required_checks_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script(POSTCHECK_SCRIPT)
    _patch_urlopen(monkeypatch, module)
    repo_root = _make_repo(tmp_path, version="0.19.0")
    output = tmp_path / "postcheck.json"

    result = module.main(
        [
            "--repo-root",
            str(repo_root),
            "--output",
            str(output),
            "--expected-version",
            "0.19.0",
            "--health-url",
            "http://127.0.0.1:8000/health",
            "--ready-url",
            "http://127.0.0.1:8000/ready",
            "--metrics-url",
            "http://127.0.0.1:8000/metrics",
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["checks"] == {"health": "ok", "ready": "ok", "metrics": "ok", "paths": "ok"}
    assert payload["limitations"] == []
    assert payload["ready_for_final_release"] is True


def test_scripts_compile() -> None:
    py_compile.compile(str(INVENTORY_SCRIPT), doraise=True)
    py_compile.compile(str(POSTCHECK_SCRIPT), doraise=True)


def test_output_contains_no_fake_secret_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script(INVENTORY_SCRIPT)
    _patch_urlopen(monkeypatch, module, body=b'token="RUNTIME_TRANSITION_FAKE_SECRET"')
    repo_root = _make_repo(tmp_path)
    output = tmp_path / "inventory.json"

    result = module.main(
        [
            "--repo-root",
            str(repo_root),
            "--output",
            str(output),
            "--include-health-url",
            "http://127.0.0.1:8000/health",
        ]
    )

    assert result == 0
    assert FAKE_SECRET not in output.read_text(encoding="utf-8")
