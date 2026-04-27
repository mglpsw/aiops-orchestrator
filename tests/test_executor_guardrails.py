from __future__ import annotations

import asyncio

import pytest

from app.adapters.docker import DockerAdapter
from app.adapters.executor_local import LocalExecutorAdapter


class _FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"ok\n", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def test_local_executor_blocks_dangerous_docker_commands_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = LocalExecutorAdapter()
    called = False

    async def fail_if_called(*args, **kwargs):  # pragma: no cover - defensive
        nonlocal called
        called = True
        raise AssertionError("create_subprocess_exec must not be called for blocked commands")

    monkeypatch.setattr("app.adapters.executor_local.asyncio.create_subprocess_exec", fail_if_called, raising=True)

    result = asyncio.run(adapter.execute("docker compose down", timeout=5))

    assert result["exit_code"] == 126
    assert result["stderr"]
    assert "blocked" in result["stderr"].lower()
    assert called is False


def test_local_executor_uses_shellless_exec_for_safe_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = LocalExecutorAdapter()
    captured: dict[str, object] = {}

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr("app.adapters.executor_local.asyncio.create_subprocess_exec", fake_exec, raising=True)

    result = asyncio.run(adapter.execute("git status --short --branch", timeout=5))

    assert result["exit_code"] == 0
    assert captured["argv"] == ("git", "status", "--short", "--branch")
    assert captured["kwargs"]["cwd"] == "/tmp"


def test_docker_adapter_is_quarantined_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = DockerAdapter()
    assert adapter.enabled is False

    async def fail_if_called(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("create_subprocess_exec must not be called while adapter is disabled")

    monkeypatch.setattr("app.adapters.docker.asyncio.create_subprocess_exec", fail_if_called, raising=True)

    result = asyncio.run(adapter.execute("ps", timeout=5))

    assert result["exit_code"] == -3
    assert "disabled" in result["stderr"].lower()


def test_docker_adapter_blocks_destructive_commands_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = DockerAdapter()
    adapter.enabled = True

    async def fail_if_called(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("create_subprocess_exec must not be called for blocked Docker commands")

    monkeypatch.setattr("app.adapters.docker.asyncio.create_subprocess_exec", fail_if_called, raising=True)

    result = asyncio.run(adapter.execute("docker compose stop", timeout=5))

    assert result["exit_code"] == 126
    assert "blocked" in result["stderr"].lower()


def test_docker_adapter_allows_read_only_diagnostics_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = DockerAdapter()
    adapter.enabled = True
    captured: dict[str, object] = {}

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProcess(stdout=b"24.0.7\n")

    monkeypatch.setattr("app.adapters.docker.asyncio.create_subprocess_exec", fake_exec, raising=True)

    result = asyncio.run(adapter.execute("version --format '{{.Server.Version}}'", timeout=5))

    assert result["exit_code"] == 0
    assert captured["argv"][0] == "docker"
    assert captured["argv"][1] == "version"
    assert captured["kwargs"]["cwd"] is None
