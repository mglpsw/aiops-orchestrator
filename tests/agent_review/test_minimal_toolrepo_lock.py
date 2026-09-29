from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = ROOT / "requirements-agent-review.lock"
INSTALL_SCRIPT = ROOT / "scripts" / "install-agent-review-toolrepo.sh"

_FORBIDDEN_PACKAGES = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "aiosqlite",
    "asyncpg",
    "psycopg",
    "psycopg2",
)

_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s\\]+)"
)


def _parse_lock() -> dict[str, dict[str, object]]:
    text = LOCK_FILE.read_text(encoding="utf-8")
    entries: dict[str, dict[str, object]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT_RE.match(line)
        if match:
            current = match.group("name").lower()
            entries[current] = {"version": match.group("version"), "hashes": []}
            continue
        if current and line.startswith("--hash=sha256:"):
            entries[current]["hashes"].append(line.removeprefix("--hash=sha256:"))
    return entries


def test_lock_file_exists_and_is_non_empty() -> None:
    assert LOCK_FILE.is_file()
    assert LOCK_FILE.read_text(encoding="utf-8").strip()


def test_every_lock_entry_has_an_exact_version_and_at_least_one_hash() -> None:
    entries = _parse_lock()
    assert entries, "lock file did not parse any requirement"
    for name, entry in entries.items():
        version = str(entry["version"])
        assert not version.endswith((".*", "*")), f"{name} is not an exact version pin"
        assert re.fullmatch(r"[0-9][0-9A-Za-z.\-+]*", version), f"{name} version {version!r} is not exact"
        hashes = entry["hashes"]
        assert hashes, f"{name} has no --hash entries"
        for digest in hashes:
            assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{name} has a malformed sha256 hash"


def test_lock_file_contains_no_runtime_only_dependencies() -> None:
    entries = _parse_lock()
    for forbidden in _FORBIDDEN_PACKAGES:
        assert forbidden not in entries, f"{forbidden} must not be part of the offline AgentReview lock"


def test_lock_file_only_declares_pydantic_and_pyyaml_and_their_direct_needs() -> None:
    entries = set(_parse_lock())
    allowed = {
        "pydantic",
        "pydantic-core",
        "annotated-types",
        "typing-extensions",
        "typing-inspection",
        "pyyaml",
    }
    assert entries <= allowed, f"unexpected packages in lock: {entries - allowed}"


def test_install_script_rejects_a_short_sha() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "/tmp/should-not-be-created-short-sha", "--toolrepo-sha", "abc1234"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not Path("/tmp/should-not-be-created-short-sha").exists()


def test_install_script_rejects_an_explicitly_empty_sha() -> None:
    """Post-merge finding (P1, Codex review of PR #98): `-n "$TOOLREPO_SHA"`
    alone treats an explicitly empty --toolrepo-sha the same as the flag
    never being passed, silently skipping pin validation. A caller that
    intended to pin (e.g. a CI variable that resolved empty) must be
    rejected, not silently downgraded to "no pin requested"."""

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "/tmp/should-not-be-created-empty-sha", "--toolrepo-sha", ""],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not Path("/tmp/should-not-be-created-empty-sha").exists()


def test_install_script_rejects_a_branch_name_as_pin() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "/tmp/should-not-be-created-branch", "--toolrepo-sha", "master"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not Path("/tmp/should-not-be-created-branch").exists()


def test_install_script_rejects_a_sha_not_matching_head() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "/tmp/should-not-be-created-wrong-sha", "--toolrepo-sha", "0" * 40],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not Path("/tmp/should-not-be-created-wrong-sha").exists()


def test_install_script_rejects_incompatible_interpreter_before_venv_creation(tmp_path: Path) -> None:
    """G1: Incompatible interpreter is rejected fail-closed before venv creation without depending on host Python."""
    fake_python = tmp_path / "fake_python312.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    echo "CPython 3.12"\n'
        '    exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    target_venv = tmp_path / "should_not_exist_venv"
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert not target_venv.exists(), "Venv must not be created when interpreter is incompatible"
    assert "requirements-agent-review.lock is qualified for CPython 3.11" in result.stderr
    assert "CPython 3.12" in result.stderr


def test_install_script_accepts_compatible_interpreter_through_version_guard(tmp_path: Path) -> None:
    """G2: Compatible interpreter traverses version guard; fails downstream at venv or completes."""
    fake_python = tmp_path / "fake_python311.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    echo "CPython 3.11"\n'
        '    exit 0\n'
        "fi\n"
        "# Reached venv creation: echo marker and exit 42\n"
        'echo "GUARD_PASSED_CALLED_WITH: $@" >&2\n'
        "exit 42\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    target_venv = tmp_path / "venv_g2"
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    # Exit code 42 proves the script passed the version check and proceeded to "$PYTHON_BIN -m venv"
    assert result.returncode == 42
    assert "requirements-agent-review.lock is qualified for CPython 3.11" not in result.stderr
    assert "GUARD_PASSED_CALLED_WITH: -m venv" in result.stderr


def test_install_script_probe_isolates_from_malicious_sitecustomize(tmp_path: Path) -> None:
    """H1: Ambient PYTHONPATH with malicious sitecustomize is ignored by isolated identity probe (-I -S)."""
    sc_dir = tmp_path / "ambient_site"
    sc_dir.mkdir()
    marker = tmp_path / "sitecustomize_executed.marker"
    sc = sc_dir / "sitecustomize.py"
    sc.write_text(
        f"import sys\n"
        f"from pathlib import Path\n"
        f'Path("{marker}").write_text("SITECUSTOMIZE_RAN", encoding="utf-8")\n'
        f'print("CPython 3.11")\n'
        f"sys.exit(0)\n",
        encoding="utf-8",
    )

    controlled_py = tmp_path / "controlled_python.sh"
    controlled_py.write_text(
        f"#!/bin/sh\n"
        f'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        f'    "{sys.executable}" -I -S -c "pass"\n'
        f'    echo "CPython 3.14"\n'
        f"    exit 0\n"
        f"fi\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    controlled_py.chmod(0o755)

    target_venv = tmp_path / "should_not_exist_venv_h1"
    env = dict(os.environ, PYTHONPATH=str(sc_dir), AGENT_REVIEW_PYTHON=str(controlled_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert not marker.exists(), "sitecustomize hook must NOT be executed by identity probe"
    assert result.returncode != 0
    assert not target_venv.exists(), "Venv must not be created when interpreter is incompatible"
    assert "requirements-agent-review.lock is qualified for CPython 3.11" in result.stderr
    assert "CPython 3.14" in result.stderr


@pytest.mark.requires_network
def test_install_script_produces_a_working_minimal_venv(tmp_path: Path) -> None:
    venv_dir = tmp_path / "agent-review-venv"
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(venv_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    python = venv_dir / "bin" / "python3"
    list_result = subprocess.run(
        [str(python), "-m", "pip", "list", "--format=freeze"],
        capture_output=True,
        text=True,
        check=False,
    )
    installed = {line.split("==")[0].lower() for line in list_result.stdout.splitlines() if "==" in line}
    for forbidden in _FORBIDDEN_PACKAGES:
        assert forbidden not in installed

    import_result = subprocess.run(
        [str(python), "-c", "import pydantic, yaml; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(ROOT)},
    )
    assert import_result.returncode == 0, import_result.stderr
    assert "ok" in import_result.stdout


@pytest.mark.requires_network
def test_require_hashes_rejects_a_tampered_lock_file(tmp_path: Path) -> None:
    """Proves --require-hashes is actually enforced, not decorative: a
    lock file with one digit flipped in a hash must fail installation."""

    tampered = tmp_path / "tampered.lock"
    original = LOCK_FILE.read_text(encoding="utf-8")
    tampered_text = original.replace(
        "--hash=sha256:1f02e8b43a8fbbc3f3e0d4f0f4bfc8131bcb4eebe8849b8e5c773f3a1c582a53",
        "--hash=sha256:0000000000000000000000000000000000000000000000000000000000000000"[:71],
        1,
    )
    assert tampered_text != original, "expected hash string not found in lock file"
    tampered.write_text(tampered_text, encoding="utf-8")

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    pip = venv_dir / "bin" / "pip"
    result = subprocess.run(
        [str(pip), "install", "--require-hashes", "--no-deps", "-r", str(tampered)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
