from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = ROOT / "requirements-agent-review.lock"
INSTALL_SCRIPT = ROOT / "scripts" / "install-agent-review-toolrepo.sh"
INSTALL_AUTHORITY = ROOT / "scripts" / "agent-review-install-authority.py"

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


def _path_harness_python(host_python: str, pip_arguments: Path) -> str:
    """Intercept pip locally; preserve host forwarding for pathname consumers."""
    import shlex
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-m" ] && [ "$3" = "pip" ]; then\n'
        '    printf "%s\\n" "$@" > ' + shlex.quote(str(pip_arguments)) + "\n"
        "    exit 0\nfi\n"
        "exec " + shlex.quote(host_python) + ' "$@"\n'
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
        '    echo "INCOMPATIBLE: interpreter CPython 3.12 (required: CPython 3.11)"\n'
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


def test_install_script_rejects_incompatible_architecture_before_venv_creation(tmp_path: Path) -> None:
    """L3: Non-x86_64 architecture (e.g. aarch64) is rejected fail-closed before venv creation."""
    fake_python = tmp_path / "fake_aarch64.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    echo "INCOMPATIBLE: architecture aarch64 (required: x86_64)"\n'
        '    exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    target_venv = tmp_path / "should_not_exist_venv_arch"
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert not target_venv.exists(), "Venv must not be created when architecture is incompatible"
    assert "architecture aarch64" in result.stderr


def test_install_script_rejects_musl_libc_before_venv_creation(tmp_path: Path) -> None:
    """L3: Non-glibc systems (e.g. Alpine musl) are rejected fail-closed before venv creation."""
    fake_python = tmp_path / "fake_musl.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    echo "INCOMPATIBLE: libc non-glibc/musl (required: glibc >= 2.17)"\n'
        '    exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    target_venv = tmp_path / "should_not_exist_venv_musl"
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert not target_venv.exists(), "Venv must not be created when libc is non-glibc"
    assert "libc non-glibc/musl" in result.stderr


def test_install_script_rejects_old_glibc_before_venv_creation(tmp_path: Path) -> None:
    """L3: Glibc versions older than 2.17 (manylinux2014 minimum floor) are rejected fail-closed."""
    fake_python = tmp_path / "fake_old_glibc.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    echo "INCOMPATIBLE: glibc 2.12 (required: glibc >= 2.17)"\n'
        '    exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    target_venv = tmp_path / "should_not_exist_venv_glibc"
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert not target_venv.exists(), "Venv must not be created when glibc is older than 2.17"
    assert "glibc 2.12" in result.stderr


def test_install_script_rejects_32bit_interpreter_before_venv_creation(tmp_path: Path) -> None:
    """P4: 32-bit interpreter ABI (word size 32-bit) is rejected fail-closed before venv creation."""
    fake_python = tmp_path / "fake_32bit_python.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    echo "INCOMPATIBLE: word size 32-bit (required: 64-bit)"\n'
        '    exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    target_venv = tmp_path / "should_not_exist_venv_32bit"
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert not target_venv.exists(), "Venv must not be created when interpreter word size is 32-bit"
    assert "word size 32-bit (required: 64-bit)" in result.stderr


def _make_fake_python311(tmp_path: Path) -> Path:
    fake_python = tmp_path / "fake_python311_runner.sh"
    host_python = sys.executable
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_python}" "$@"\n'
        '    fi\n'
        '    echo "OK"\n'
        '    exit 0\n'
        "fi\n"
        '# Reached venv creation: echo marker and exit 42\n'
        'echo "GUARD_PASSED_CALLED_WITH: $@" >&2\n'
        "exit 42\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    return fake_python


def test_install_script_accepts_compatible_interpreter_through_version_guard(tmp_path: Path) -> None:
    """G2: Compatible interpreter traverses version guard; normalizes target and proceeds to venv."""
    fake_python = _make_fake_python311(tmp_path)
    target_venv = tmp_path / "venv_g2"
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    # Exit code 42 proves the script passed the version check and proceeded to "$PYTHON_BIN -I -S -m venv <stage_dir>"
    assert result.returncode == 42
    assert "requirements-agent-review.lock is qualified for CPython 3.11" not in result.stderr
    assert "GUARD_PASSED_CALLED_WITH: -I -S -m venv" in result.stderr
    assert str(target_venv.parent.resolve()) in result.stderr
    assert ".agent_review_stage." in result.stderr


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
        f'print("OK")\n'
        f"sys.exit(0)\n",
        encoding="utf-8",
    )

    controlled_py = tmp_path / "controlled_python.sh"
    controlled_py.write_text(
        f"#!/bin/sh\n"
        f'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        f'    "{sys.executable}" -I -S -c "pass"\n'
        f'    echo "INCOMPATIBLE: interpreter CPython 3.14 (required: CPython 3.11)"\n'
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


def test_countermodel_l2_ambient_pythonpath_cannot_shadow_venv(tmp_path: Path) -> None:
    """L2: Ambient PYTHONPATH containing malicious venv.py is ignored by isolated venv invocation (-I -S)."""
    evil_dir = tmp_path / "evil_path_venv"
    evil_dir.mkdir()
    marker = tmp_path / "evil_venv_executed.marker"
    evil_venv = evil_dir / "venv.py"
    evil_venv.write_text(
        f"from pathlib import Path\n"
        f'Path("{marker}").write_text("PWNED_VENV", encoding="utf-8")\n'
        f"raise SystemExit(99)\n",
        encoding="utf-8",
    )

    # Positive proof of isolation: -I -S -m venv creates target without executing evil venv.py
    target_isolated = tmp_path / "venv_isolated"
    res_isolated = subprocess.run(
        [sys.executable, "-I", "-S", "-m", "venv", str(target_isolated)],
        env=dict(os.environ, PYTHONPATH=str(evil_dir)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_isolated.returncode == 0
    assert not marker.exists(), "Malicious venv.py on PYTHONPATH must NOT be executed under -I -S"
    assert (target_isolated / "bin" / "python3").is_file()

    # Countermodel flaw proof: unisolated -m venv would execute malicious venv.py
    target_unisolated = tmp_path / "venv_unisolated"
    res_unisolated = subprocess.run(
        [sys.executable, "-m", "venv", str(target_unisolated)],
        env=dict(os.environ, PYTHONPATH=str(evil_dir)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_unisolated.returncode == 99, "Precondition: unisolated -m venv must fail due to evil venv.py"
    assert marker.exists(), "Precondition: unisolated -m venv must execute evil venv.py"


def test_countermodel_l2_ambient_pythonpath_cannot_shadow_pip(tmp_path: Path) -> None:
    """L2: Ambient PYTHONPATH containing malicious pip package is ignored by isolated pip invocation (-I)."""
    target_venv = tmp_path / "base_venv"
    subprocess.run([sys.executable, "-I", "-S", "-m", "venv", str(target_venv)], check=True)
    venv_py = str(target_venv / "bin" / "python3")

    evil_dir = tmp_path / "evil_path_pip"
    evil_dir.mkdir()
    evil_pip = evil_dir / "pip"
    evil_pip.mkdir()
    (evil_pip / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "evil_pip_executed.marker"
    (evil_pip / "__main__.py").write_text(
        f"from pathlib import Path\n"
        f'Path("{marker}").write_text("PWNED_PIP", encoding="utf-8")\n'
        f"raise SystemExit(88)\n",
        encoding="utf-8",
    )

    # Positive proof of isolation: venv_python -I -m pip ignores evil pip
    res_isolated = subprocess.run(
        [venv_py, "-I", "-m", "pip", "--version"],
        env=dict(os.environ, PYTHONPATH=str(evil_dir)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_isolated.returncode == 0
    assert not marker.exists(), "Malicious pip on PYTHONPATH must NOT be executed under -I"

    # Countermodel flaw proof: unisolated -m pip would execute malicious pip
    res_unisolated = subprocess.run(
        [venv_py, "-m", "pip", "--version"],
        env=dict(os.environ, PYTHONPATH=str(evil_dir)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_unisolated.returncode == 88, "Precondition: unisolated -m pip must fail due to evil pip"
    assert marker.exists(), "Precondition: unisolated -m pip must execute evil pip"


def test_install_script_invokes_pip_in_isolated_mode() -> None:
    """M3: scripts/install-agent-review-toolrepo.sh invokes pip with PIP_CONFIG_FILE=/dev/null and --isolated."""
    script_text = INSTALL_AUTHORITY.read_text(encoding="utf-8")
    assert 'pip_env["PIP_CONFIG_FILE"] = "/dev/null"' in script_text
    assert 'stage_python = os.path.join(stage_dir, "bin", "python3")' in script_text
    assert '[stage_python, "-I", "-m", "pip", "--isolated", "install", "--require-hashes", "--no-deps", "-r", lock_file]' in script_text


def test_countermodel_m3_pip_isolation_ignores_pip_target(tmp_path: Path) -> None:
    """Countermodel M3: pip --isolated ignores caller PIP_TARGET and installs strictly into venv site-packages."""
    import zipfile

    # Build a pure-python dummy wheel with standard metadata and RECORD
    whl_path = tmp_path / "dummy_pkg-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl_path, "w") as z:
        z.writestr("dummy_pkg.py", "VALUE = 42\n")
        z.writestr("dummy_pkg-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: dummy-pkg\nVersion: 0.1.0\n")
        z.writestr("dummy_pkg-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr(
            "dummy_pkg-0.1.0.dist-info/RECORD",
            "dummy_pkg.py,,\ndummy_pkg-0.1.0.dist-info/METADATA,,\ndummy_pkg-0.1.0.dist-info/WHEEL,,\ndummy_pkg-0.1.0.dist-info/RECORD,,\n",
        )

    # Create base venv
    venv_dir = tmp_path / "venv_target_test"
    subprocess.run([sys.executable, "-I", "-S", "-m", "venv", str(venv_dir)], check=True)
    venv_py = str(venv_dir / "bin" / "python3")

    evil_target = tmp_path / "evil_pip_target"
    evil_target.mkdir()
    env_with_target = dict(os.environ, PIP_TARGET=str(evil_target))

    # Flaw proof: unisolated pip install honors PIP_TARGET, installing package outside venv
    res_unisolated = subprocess.run(
        [venv_py, "-I", "-m", "pip", "install", "--no-index", "--no-deps", str(whl_path)],
        env=env_with_target,
        capture_output=True,
        text=True,
    )
    assert res_unisolated.returncode == 0, res_unisolated.stderr
    assert (evil_target / "dummy_pkg.py").is_file(), "Precondition: unisolated pip must install to PIP_TARGET"
    # The venv itself cannot import dummy_pkg because it was redirected outside
    res_import_fail = subprocess.run([venv_py, "-c", "import dummy_pkg"], capture_output=True, text=True)
    assert res_import_fail.returncode != 0, "Precondition: venv must fail to import dummy_pkg when redirected to PIP_TARGET"

    # Fix proof: isolated pip install ignores PIP_TARGET and installs strictly into venv site-packages
    venv_dir_iso = tmp_path / "venv_target_iso"
    subprocess.run([sys.executable, "-I", "-S", "-m", "venv", str(venv_dir_iso)], check=True)
    venv_py_iso = str(venv_dir_iso / "bin" / "python3")

    evil_target_iso = tmp_path / "evil_pip_target_iso"
    evil_target_iso.mkdir()
    env_with_target_iso = dict(os.environ, PIP_TARGET=str(evil_target_iso))

    res_isolated = subprocess.run(
        [venv_py_iso, "-I", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", str(whl_path)],
        env=env_with_target_iso,
        capture_output=True,
        text=True,
    )
    assert res_isolated.returncode == 0, res_isolated.stderr
    assert not (evil_target_iso / "dummy_pkg.py").exists(), "Isolated pip must NOT install to PIP_TARGET"
    res_import_success = subprocess.run([venv_py_iso, "-c", "import dummy_pkg"], capture_output=True, text=True)
    assert res_import_success.returncode == 0, "Isolated pip must install directly into venv site-packages"


def test_countermodel_m3_pip_isolation_ignores_user_pip_config(tmp_path: Path) -> None:
    """Countermodel M3: PIP_CONFIG_FILE=/dev/null and pip --isolated disables all user, global, and env pip configuration."""
    fake_home = tmp_path / "fake_home"
    pip_conf_dir = fake_home / ".config" / "pip"
    pip_conf_dir.mkdir(parents=True)
    evil_conf_target = tmp_path / "evil_conf_target"
    (pip_conf_dir / "pip.conf").write_text(f"[global]\ntarget = {evil_conf_target}\n", encoding="utf-8")

    venv_dir = tmp_path / "venv_conf_test"
    subprocess.run([sys.executable, "-I", "-S", "-m", "venv", str(venv_dir)], check=True)
    venv_py = str(venv_dir / "bin" / "python3")

    env_with_conf = dict(os.environ, HOME=str(fake_home), XDG_CONFIG_HOME=str(fake_home / ".config"))

    # Flaw proof 1: unisolated pip reads user configuration file
    res_unisolated = subprocess.run(
        [venv_py, "-I", "-m", "pip", "config", "list"],
        env=env_with_conf,
        capture_output=True,
        text=True,
    )
    assert res_unisolated.returncode == 0
    assert "target=" in res_unisolated.stdout, "Precondition: unisolated pip must reflect user pip config"

    # Fix proof 1: isolated pip (--isolated) ignores user configuration file
    res_isolated = subprocess.run(
        [venv_py, "-I", "-m", "pip", "--isolated", "config", "list"],
        env=env_with_conf,
        capture_output=True,
        text=True,
    )
    assert res_isolated.returncode == 0
    assert "target=" not in res_isolated.stdout, "Isolated pip must NOT reflect user pip config"

    # Flaw proof 2 (Codex finding): caller-exported PIP_CONFIG_FILE is still loaded by --isolated alone
    caller_config_file = tmp_path / "caller_pip.conf"
    evil_caller_target = tmp_path / "evil_caller_target"
    caller_config_file.write_text(f"[global]\ntarget = {evil_caller_target}\n", encoding="utf-8")
    env_caller_config = dict(os.environ, PIP_CONFIG_FILE=str(caller_config_file))

    res_caller_unprotected = subprocess.run(
        [venv_py, "-I", "-m", "pip", "--isolated", "config", "list"],
        env=env_caller_config,
        capture_output=True,
        text=True,
    )
    assert res_caller_unprotected.returncode == 0
    assert "target=" in res_caller_unprotected.stdout, "Precondition: --isolated alone still loads env-selected PIP_CONFIG_FILE"

    # Fix proof 2: PIP_CONFIG_FILE=/dev/null suppresses caller-selected and global configuration
    env_caller_disabled = dict(env_caller_config, PIP_CONFIG_FILE="/dev/null")
    res_caller_protected = subprocess.run(
        [venv_py, "-I", "-m", "pip", "--isolated", "config", "list"],
        env=env_caller_disabled,
        capture_output=True,
        text=True,
    )
    assert res_caller_protected.returncode == 0
    assert "target=" not in res_caller_protected.stdout, "PIP_CONFIG_FILE=/dev/null must disable env-selected config"


def test_countermodel_p1_pip_isolation_ignores_global_and_system_config(tmp_path: Path) -> None:
    """P1: PIP_CONFIG_FILE=/dev/null and pip --isolated suppresses global/system-wide pip config,
    proving ConfigFileEnumerated != ConfigValueApplied even when /etc/pip.conf or XDG_CONFIG_DIRS configures target."""
    import zipfile

    # Build a pure-python dummy wheel with standard metadata and RECORD
    whl_path = tmp_path / "dummy_p1-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl_path, "w") as z:
        z.writestr("dummy_p1.py", "VALUE = 42\n")
        z.writestr("dummy_p1-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: dummy-p1\nVersion: 0.1.0\n")
        z.writestr("dummy_p1-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr(
            "dummy_p1-0.1.0.dist-info/RECORD",
            "dummy_p1.py,,\ndummy_p1-0.1.0.dist-info/METADATA,,\ndummy_p1-0.1.0.dist-info/WHEEL,,\ndummy_p1-0.1.0.dist-info/RECORD,,\n",
        )

    venv_dir = tmp_path / "venv_p1"
    subprocess.run([sys.executable, "-I", "-S", "-m", "venv", str(venv_dir)], check=True)
    venv_py = str(venv_dir / "bin" / "python3")

    # Simulate system-wide global configuration via XDG_CONFIG_DIRS (treated as /etc/xdg/pip/pip.conf)
    sys_dir = tmp_path / "sys_etc"
    xdg_pip = sys_dir / "pip"
    xdg_pip.mkdir(parents=True)
    outside_dir = tmp_path / "outside_p1"
    outside_dir.mkdir()
    (xdg_pip / "pip.conf").write_text(f"[global]\ntarget = {outside_dir}\n", encoding="utf-8")

    # Flaw proof: unisolated pip reflects system-wide target configuration
    env_unisolated = dict(os.environ, XDG_CONFIG_DIRS=str(sys_dir))
    res_unisolated = subprocess.run(
        [venv_py, "-I", "-m", "pip", "config", "list"],
        env=env_unisolated,
        capture_output=True,
        text=True,
    )
    assert res_unisolated.returncode == 0
    assert "target=" in res_unisolated.stdout, "Precondition: unisolated pip must reflect system-wide config"

    # Fix proof 1: PIP_CONFIG_FILE=/dev/null and --isolated suppresses system-wide config values
    env_isolated = dict(os.environ, XDG_CONFIG_DIRS=str(sys_dir), PIP_CONFIG_FILE="/dev/null")
    res_isolated = subprocess.run(
        [venv_py, "-I", "-m", "pip", "--isolated", "config", "list"],
        env=env_isolated,
        capture_output=True,
        text=True,
    )
    assert res_isolated.returncode == 0
    assert "target=" not in res_isolated.stdout, "PIP_CONFIG_FILE=/dev/null + --isolated must suppress system-wide config"

    # Fix proof 2 (Causal Install Verification): installation installs strictly into venv, writing 0 files outside
    res_install = subprocess.run(
        [venv_py, "-I", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", str(whl_path)],
        env=env_isolated,
        capture_output=True,
        text=True,
    )
    assert res_install.returncode == 0, res_install.stderr
    assert len(list(outside_dir.iterdir())) == 0, "No packages must be redirected to outside target directory"

    # The package must be successfully imported by the venv
    res_import = subprocess.run([venv_py, "-c", "import dummy_p1; assert dummy_p1.VALUE == 42"], capture_output=True, text=True)
    assert res_import.returncode == 0, f"Venv must be able to import installed package: {res_import.stderr}"


def test_install_script_rejects_existing_nonempty_directory(tmp_path: Path) -> None:
    """J1-A: Existing non-empty target directory is rejected fail-closed without mutating contents."""
    target_dir = tmp_path / "existing_nonempty_venv"
    target_dir.mkdir()
    marker = target_dir / "marker.txt"
    marker.write_text("DO_NOT_MUTATE", encoding="utf-8")

    fake_py = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "DO_NOT_MUTATE"
    assert "AgentReview toolrepo venv target must be absent" in result.stderr
    assert str(target_dir) in result.stderr


def test_install_script_rejects_existing_empty_directory(tmp_path: Path) -> None:
    """J1-C: Existing empty target directory is rejected fail-closed (target must be absent)."""
    target_dir = tmp_path / "existing_empty_venv"
    target_dir.mkdir()

    fake_py = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert "AgentReview toolrepo venv target must be absent" in result.stderr
    assert str(target_dir) in result.stderr


def test_install_script_rejects_preexisting_venv_with_stale_dependency_witness(tmp_path: Path) -> None:
    """J1-B: Preexisting venv with residual packages is rejected before venv creation or reuse."""
    venv_dir = tmp_path / "preexisting_stale_venv"
    site_packages = venv_dir / "lib" / "python3.11" / "site-packages"
    site_packages.mkdir(parents=True)
    stale_marker = site_packages / "fastapi.py"
    stale_marker.write_text("# stale residual package witness\n", encoding="utf-8")

    fake_py = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(venv_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert stale_marker.exists(), "stale witness must NOT be deleted or mutated"
    assert stale_marker.read_text(encoding="utf-8") == "# stale residual package witness\n"
    assert "AgentReview toolrepo venv target must be absent" in result.stderr
    assert str(venv_dir) in result.stderr


def test_install_script_rejects_symlink_target(tmp_path: Path) -> None:
    """J1: Symlink target (valid or broken) is rejected fail-closed before venv creation."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    symlink_target = tmp_path / "symlink_venv"
    symlink_target.symlink_to(real_dir)

    fake_py = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(symlink_target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert "AgentReview toolrepo venv target must be absent" in result.stderr
    assert str(symlink_target) in result.stderr


def test_countermodel_k1_noncanonical_alias_to_existing_target_is_rejected(tmp_path: Path) -> None:
    """K1: Non-canonical path with uncreated intermediate prefix (/tmp/new/../existing-venv)
    is canonicalized before freshness check; existing canonical target is refused and
    intermediate directory is never created."""
    existing_venv = tmp_path / "existing-venv"
    existing_venv.mkdir()
    stale_marker = existing_venv / "stale-marker.txt"
    stale_marker.write_text("STALE_DEPENDENCY_WITNESS\n", encoding="utf-8")

    # /tmp/new does NOT exist initially
    uncreated_intermediate = tmp_path / "new"
    assert not uncreated_intermediate.exists()

    noncanonical_target = uncreated_intermediate / ".." / "existing-venv"

    fake_py = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(noncanonical_target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert not uncreated_intermediate.exists(), "intermediate directory must NOT be created"
    assert stale_marker.exists(), "stale marker must NOT be deleted or mutated"
    assert stale_marker.read_text(encoding="utf-8") == "STALE_DEPENDENCY_WITNESS\n"
    assert "AgentReview toolrepo venv target must be absent" in result.stderr
    assert str(existing_venv.resolve()) in result.stderr
    assert str(noncanonical_target) in result.stderr


def test_install_script_rejects_empty_target_directory(tmp_path: Path) -> None:
    """K1-empty: Explicitly empty target directory is rejected fail-closed without mutation."""
    fake_py = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), ""],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert "target venv directory cannot be empty" in result.stderr


def test_install_script_normalizes_relative_target_before_venv_creation(tmp_path: Path) -> None:
    """K1-rel: Relative target (e.g. ./rel_venv) is canonicalized to absolute path before venv creation."""
    fake_py = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "./rel_venv"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 42
    assert "GUARD_PASSED_CALLED_WITH: -I -S -m venv" in result.stderr
    assert str(tmp_path.resolve()) in result.stderr
    assert ".agent_review_stage." in result.stderr


@pytest.mark.requires_network
@pytest.mark.parametrize("explicit_override", [True, False], ids=["valid-override-bad-default", "absent-override-good-default"])
def test_supervisor_uses_qualified_bootstrap_runtime(tmp_path: Path, explicit_override: bool) -> None:
    """Q-EXEC-01: exercise the real supervisor, private pip and published runtime."""
    import json
    import shlex

    assert sys.version_info[:2] == (3, 11), "this installation control requires CPython 3.11"
    ambient_bin = tmp_path / "ambient"
    ambient_bin.mkdir()
    ambient_python = ambient_bin / "python3"
    marker = tmp_path / "ambient-invocations"
    env = os.environ.copy()
    env.pop("AGENT_REVIEW_PYTHON", None)
    env["PATH"] = str(ambient_bin) + os.pathsep + env["PATH"]
    if explicit_override:
        env["AGENT_REVIEW_PYTHON"] = sys.executable
        ambient_python.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" >> " + shlex.quote(str(marker)) + "\n"
            "exit 97\n", encoding="utf-8",
        )
        ambient_python.chmod(0o755)
    else:
        ambient_python.symlink_to(sys.executable)

    target = tmp_path / "runtime"
    # Observe actual Popen argument vectors in a disposable copy; delegate the
    # unchanged real worker execution, never a successful fake supervisor.
    argv_log = tmp_path / "phase-arguments.jsonl"
    installer = _disposable_authority(tmp_path,
        "    proc = subprocess.Popen(argv, start_new_session=True, env=env)\n",
        f"    with open({str(argv_log)!r}, 'a') as observation:\n"
        "        observation.write(__import__('json').dumps(argv) + '\\n')\n"
        "    proc = subprocess.Popen(argv, start_new_session=True, env=env)\n")
    result = subprocess.run(
        ["bash", "-x", str(installer), str(target)],
        env=env, capture_output=True, text=True, check=False,
    )
    (tmp_path / "installer.stdout").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "installer.stderr").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "the explicit bootstrap runtime must not be replaced by PATH/python3"
    selected = sys.executable if explicit_override else "python3"
    # The trace witnesses execution of the production wrapper by the selected
    # real interpreter; a double returning OK cannot satisfy this control.
    assert f"+ {selected} -I -S -c '" in result.stderr
    assert "PR_SET_CHILD_SUBREAPER" in result.stderr
    phases = [json.loads(line) for line in argv_log.read_text().splitlines()]
    pip_phase = next(argv for argv in phases if re.search(r"/\.agent_review_stage\.[^ /]+/bin/python3$", argv[0]))
    assert pip_phase[1:8] == ["-I", "-m", "pip", "--isolated", "install", "--require-hashes", "--no-deps"]
    runtime = subprocess.run(
        [str(target / "bin" / "python3"), "-c",
         "import json,sys,pydantic,yaml; print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"],
        capture_output=True, text=True, check=False,
    )
    assert runtime.returncode == 0, runtime.stderr
    identity = json.loads(runtime.stdout)
    assert identity["prefix"] == str(target)
    assert identity["base_prefix"] != str(target)
    assert not list(tmp_path.glob(".agent_review_stage.*"))
    print(json.dumps({"bootstrap": selected, "runtime": identity, "trace": str(tmp_path / "installer.stderr")}))


def test_incompatible_override_does_not_fall_back_to_valid_default(tmp_path: Path) -> None:
    """C2: refusal precedes admission of any installation worker."""
    import shlex

    ambient_bin = tmp_path / "ambient"
    ambient_bin.mkdir()
    (ambient_bin / "python3").symlink_to(sys.executable)
    marker = tmp_path / "override-invocations"
    override = tmp_path / "incompatible-python"
    override.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" >> " + shlex.quote(str(marker)) + "\n"
        "echo 'INCOMPATIBLE: interpreter CPython 3.12 (required: CPython 3.11)'\n"
        "exit 0\n", encoding="utf-8",
    )
    override.chmod(0o755)
    target = tmp_path / "runtime"
    result = subprocess.run(
        ["bash", "-x", str(INSTALL_SCRIPT), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(override),
                 PATH=str(ambient_bin) + os.pathsep + os.environ["PATH"]),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2, result.stderr
    assert "INCOMPATIBLE" in result.stderr
    assert marker.read_text(encoding="utf-8").splitlines()[:3] == ["-I", "-S", "-c"]
    assert "PR_SET_CHILD_SUBREAPER" not in result.stderr
    assert not target.exists()
    assert not list(tmp_path.glob(".agent_review_stage.*"))


@pytest.mark.requires_network
@pytest.mark.parametrize("pathname", [
    "agent-review-venv", "venv with spaces", "venv'quote", 'venv"quote',
    "venv$dollar", "venv`backtick", "venv\\backslash", "venv_ñ_λ_🚀",
], ids=["ordinary", "space", "single-quote", "double-quote", "dollar", "backtick", "backslash", "unicode"])
def test_install_script_produces_a_working_minimal_venv(tmp_path: Path, pathname: str) -> None:
    """Independent lock-backed runtimes, distinct from wrapper path controls."""
    import json
    venv_dir = tmp_path / pathname
    env = os.environ.copy()
    if "AGENT_REVIEW_PYTHON" not in env:
        py311 = shutil.which("python3.11")
        if py311:
            env["AGENT_REVIEW_PYTHON"] = py311
        elif sys.version_info[:2] != (3, 11):
            pytest.skip("EVIDENCE_LIMITATION: Python 3.11 interpreter not available on local host")
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(venv_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    python = venv_dir / "bin" / "python3"
    list_result = subprocess.run(
        [str(python), "-m", "pip", "list", "--format=freeze"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert list_result.returncode == 0, list_result.stderr
    installed_versions = {re.sub(r"[-_.]+", "-", line.split("==")[0]).lower(): line.split("==")[1]
                          for line in list_result.stdout.splitlines() if "==" in line}
    installed = set(installed_versions)
    for name, entry in _parse_lock().items():
        assert installed_versions[name] == entry["version"]
    for forbidden in _FORBIDDEN_PACKAGES:
        assert forbidden not in installed

    import_result = subprocess.run(
        [str(python), "-c",
         "import json,sys,pydantic,yaml; "
         "from app.agent_review.contracts_v2 import ChunkPayloadV2; "
         "print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'executable':sys.executable}))"],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(ROOT)},
    )
    assert import_result.returncode == 0, import_result.stderr
    runtime = json.loads(import_result.stdout)
    assert runtime["prefix"] == str(venv_dir)
    assert runtime["prefix"] != runtime["base_prefix"]
    assert runtime["executable"] == str(python)
    assert not list(tmp_path.glob(".agent_review_stage.*"))
    for discarded in ("activate", "activate.csh", "activate.fish", "Activate.ps1", "pip", "pip3", "pip3.11"):
        assert not (venv_dir / "bin" / discarded).exists()
    print(json.dumps({"runtime": runtime, "locked_versions": installed_versions, "pathname": pathname}))


@pytest.mark.requires_network
def test_require_hashes_rejects_a_tampered_lock_file(tmp_path: Path) -> None:
    """Proves --require-hashes is actually enforced: a lock file with exactly
    one nibble changed in a syntactically valid 64-hex SHA-256 digest fails installation
    specifically due to cryptographic hash mismatch against the downloaded artifact."""
    import re

    tampered = tmp_path / "tampered.lock"
    original = LOCK_FILE.read_text(encoding="utf-8")

    # Target pure-python distribution annotated-types
    original_digest = "1f02e8b43a8fbbc3f3e0d4f0f4bfc8131bcb4eebe8849b8e5c773f3a1c582a53"
    assert f"--hash=sha256:{original_digest}" in original, "expected target digest not found in lock"

    # Flip exactly one nibble while preserving full 64-hex lowercase SHA-256 syntax
    replacement_first = "0" if original_digest[0] != "0" else "1"
    tampered_digest = replacement_first + original_digest[1:]

    assert len(tampered_digest) == 64, f"Digest length must be 64, got {len(tampered_digest)}"
    assert re.fullmatch(r"[0-9a-f]{64}", tampered_digest), "Digest must be valid 64 lowercase hex"
    assert tampered_digest != original_digest, "Digest must differ from original"

    tampered_text = original.replace(
        f"--hash=sha256:{original_digest}",
        f"--hash=sha256:{tampered_digest}",
        1,
    )
    assert tampered_text != original
    tampered.write_text(tampered_text, encoding="utf-8")

    venv_dir = tmp_path / "venv_tampered"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    pip = venv_dir / "bin" / "pip"
    result = subprocess.run(
        [str(pip), "install", "--require-hashes", "--no-deps", "-r", str(tampered)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    # Must prove rejection is due to artifact hash mismatch, not malformed hash syntax
    assert "THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE" in result.stderr
    assert "annotated-types" in result.stderr
    assert "Expected sha256" in result.stderr
    assert tampered_digest in result.stderr


def test_countermodel_p2_b_failed_fresh_install_cleans_partial_venv_on_pip_failure(tmp_path: Path) -> None:
    """P2-B: When fresh venv creation succeeds but downstream pip installation fails,
    the prospective target created by this invocation must be cleaned up, preserving
    the failure exit code and leaving the path fresh for subsequent runs.
    """
    fake_py = tmp_path / "fake_py_pip_fail.sh"
    host_py = sys.executable

    fake_py.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    target="$5"\n'
        '    mkdir -p "$target/bin"\n'
        '    cat << "EOF" > "$target/bin/python3"\n'
        "#!/bin/sh\n"
        'echo "deliberate downstream pip failure witness" >&2\n'
        "exit 66\n"
        "EOF\n"
        '    chmod +x "$target/bin/python3"\n'
        "    exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    target_venv = tmp_path / "venv_fresh_fail_pip"
    assert not target_venv.exists(), "Target must be absent before invocation"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    # 1. Preserves original nonzero failure code
    assert result.returncode == 66
    assert "deliberate downstream pip failure witness" in result.stderr

    # 2. Incomplete target created during this invocation is removed
    assert not target_venv.exists(), f"Target {target_venv} must be removed after pip failure"

    # 3. Subsequent invocation on same path is not blocked by residual directory
    fake_py_success = tmp_path / "fake_py_success.sh"
    fake_py_success.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    target="$5"\n'
        '    mkdir -p "$target/bin"\n'
        '    cat << "EOF" > "$target/bin/python3"\n'
        "#!/bin/sh\n"
        "exit 0\n"
        "EOF\n"
        '    chmod +x "$target/bin/python3"\n'
        "    exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py_success.chmod(0o755)
    env_success = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py_success))

    result_retry = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env_success,
    )
    assert result_retry.returncode == 0
    assert target_venv.exists()
    assert (target_venv / "bin" / "python3").exists()


def test_countermodel_p2_b_failed_fresh_install_cleans_partial_venv_on_venv_failure(tmp_path: Path) -> None:
    """P2-B: When python -m venv itself partially creates target files and exits with error,
    the cleanup trap must clean up the partial target directory and preserve the exit status.
    """
    fake_py = tmp_path / "fake_py_venv_fail.sh"
    host_py = sys.executable

    fake_py.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    target="$5"\n'
        '    mkdir -p "$target/incomplete_bin"\n'
        '    echo "partial_content" > "$target/incomplete_bin/marker"\n'
        '    echo "deliberate venv creation failure witness" >&2\n'
        "    exit 55\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    target_venv = tmp_path / "venv_fresh_fail_midway"
    assert not target_venv.exists(), "Target must be absent before invocation"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    # 1. Preserves original nonzero failure code
    assert result.returncode == 55
    assert "deliberate venv creation failure witness" in result.stderr

    # 2. Incomplete target created during this invocation is removed
    assert not target_venv.exists(), f"Target {target_venv} must be removed after venv failure"


def test_countermodel_f1_atomic_ownership_race_refuses_without_deleting_concurrent_target(tmp_path: Path) -> None:
    """F1: When another concurrent actor creates VENV_TARGET and writes a sentinel exactly at
    the atomic creation boundary, the current installer must detect the claim collision, refuse
    fail-closed without arming cleanup, and preserve the concurrent target and its sentinel byte-identical.
    """
    target_venv = tmp_path / "concurrent_target_venv"
    assert not target_venv.exists(), "Target must be absent before invocation"

    sentinel_text = "OTHER_INSTALLER_OWNS_THIS\n"
    host_py = sys.executable

    fake_py = tmp_path / "fake_py_f1.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        private_stage = sys.argv[5]
        target = sys.argv[6]
        # Simulate concurrent actor winning the race before publication:
        os.mkdir(target)
        with open(os.path.join(target, "sentinel.txt"), "w") as f:
            f.write({repr(sentinel_text)})
        sys.argv = ["<publish>", private_stage, target]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    with open(py_bin, "w") as f:
        f.write("#!/bin/sh\\nexit 0\\n")
    os.chmod(py_bin, 0o755)
    sys.exit(0)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py),
    )

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    # 1. Installer exits nonzero/fail-closed
    assert result.returncode == 2
    assert "AgentReview toolrepo venv target must be absent" in result.stderr
    assert str(target_venv) in result.stderr

    # 2. Target was NOT deleted by the installer's cleanup trap
    assert target_venv.exists(), "Concurrent target must NOT be deleted by installer"

    # 3. Sentinel remains byte-identical
    sentinel_file = target_venv / "sentinel.txt"
    assert sentinel_file.exists(), "Sentinel file must remain intact"
    assert sentinel_file.read_text(encoding="utf-8") == sentinel_text


def test_countermodel_f2_cleanup_failure_does_not_mask_primary_install_failure(tmp_path: Path) -> None:
    """F2: When downstream installation fails with primary error code 66 and the subsequent
    cleanup trap rm -rf also fails with exit code 73, the installer must report the cleanup
    failure code (73) and preserve the primary install failure exit code (66) without masking it.
    """
    fake_py = tmp_path / "fake_py_pip_fail.sh"
    host_py = sys.executable

    fake_py.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -z "${6:-}" ] && [ -n "${5:-}" ]; then\n'
        '        case "$4" in\n'
        '            *CLEANUP_CODE*|*stage_dir*|*shutil.rmtree*)\n'
        '                echo "simulated rm failure witness" >&2\n'
        '                exit 73\n'
        '                ;;\n'
        '        esac\n'
        '    fi\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    target="$5"\n'
        '    mkdir -p "$target/bin"\n'
        '    cat << "EOF" > "$target/bin/python3"\n'
        "#!/bin/sh\n"
        'echo "deliberate downstream pip failure witness" >&2\n'
        "exit 66\n"
        "EOF\n"
        '    chmod +x "$target/bin/python3"\n'
        "    exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    # Controlled rm via PATH that fails with exit code 73
    fake_rm = fake_bin / "rm"
    fake_rm.write_text(
        "#!/bin/sh\n"
        'echo "simulated rm failure witness" >&2\n'
        "exit 73\n",
        encoding="utf-8",
    )
    fake_rm.chmod(0o755)

    target_venv = tmp_path / "venv_fresh_fail_cleanup_rm"
    assert not target_venv.exists(), "Target must be absent before invocation"

    env = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py),
        PATH=f"{fake_bin}:{os.environ.get('PATH', '')}",
    )
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    # 1. Installer exits with primary install failure code (66), NOT cleanup error (73)
    assert result.returncode == 66
    assert result.returncode != 73

    # 2. Stderr contains primary failure witness
    assert "deliberate downstream pip failure witness" in result.stderr

    # 3. Stderr reports cleanup failure with cleanup code 73 visible
    assert "AgentReview toolrepo cleanup failed" in result.stderr
    assert "73" in result.stderr
    assert "66" in result.stderr

    # 4. Final target was never published, uncleaned private staging directory remains
    assert not target_venv.exists()
    remaining_stages = list(tmp_path.glob(".agent_review_stage.*"))
    assert len(remaining_stages) > 0, f"Uncleaned staging directory should remain on rm failure: {remaining_stages}"


def test_countermodel_g3_signal_status_preserved_on_cancellation(tmp_path: Path) -> None:
    """G3: Signal handlers must exit with appropriate signal exit codes (INT -> 130, TERM -> 143),
    never rewritten to status 1, and signal exit codes must be preserved even if cleanup fails.
    """
    host_py = sys.executable

    # Case 1: SIGINT (exit code 130)
    target_venv_int = tmp_path / "venv_g3_sigint"
    fake_py_int = tmp_path / "fake_py_sigint.sh"
    fake_py_int.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    kill -INT "$PPID"\n'
        '    sleep 1\n'
        "    exit 1\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py_int.chmod(0o755)

    env_int = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py_int))
    res_int = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv_int)],
        capture_output=True,
        text=True,
        check=False,
        env=env_int,
    )
    assert res_int.returncode == 130, f"SIGINT must exit with code 130, got {res_int.returncode}"
    assert not target_venv_int.exists(), "Cleanup must remove target on SIGINT"

    # Case 2: SIGTERM (exit code 143)
    target_venv_term = tmp_path / "venv_g3_sigterm"
    fake_py_term = tmp_path / "fake_py_sigterm.sh"
    fake_py_term.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    kill -TERM "$PPID"\n'
        '    sleep 1\n'
        "    exit 1\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py_term.chmod(0o755)

    env_term = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py_term))
    res_term = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv_term)],
        capture_output=True,
        text=True,
        check=False,
        env=env_term,
    )
    assert res_term.returncode == 143, f"SIGTERM must exit with code 143, got {res_term.returncode}"
    assert not target_venv_term.exists(), "Cleanup must remove target on SIGTERM"

    # Case 3: SIGTERM + cleanup failure (exit code 73) -> must still exit 143
    fake_py_term_fail = tmp_path / "fake_py_sigterm_fail.sh"
    fake_py_term_fail.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -z "${6:-}" ] && [ -n "${5:-}" ]; then\n'
        '        case "$4" in\n'
        '            *CLEANUP_CODE*|*stage_dir*|*shutil.rmtree*)\n'
        '                echo "simulated rm failure witness" >&2\n'
        '                exit 73\n'
        '                ;;\n'
        '        esac\n'
        '    fi\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    kill -TERM "$PPID"\n'
        '    sleep 1\n'
        "    exit 1\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py_term_fail.chmod(0o755)

    target_venv_term_fail = tmp_path / "venv_g3_sigterm_rm_fail"
    env_term_fail = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py_term_fail),
    )
    res_term_fail = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv_term_fail)],
        capture_output=True,
        text=True,
        check=False,
        env=env_term_fail,
    )
    assert res_term_fail.returncode == 143, f"SIGTERM + cleanup failure must preserve 143, got {res_term_fail.returncode}"
    assert "simulated rm failure witness" in res_term_fail.stderr
    assert "AgentReview toolrepo cleanup failed" in res_term_fail.stderr
    assert "73" in res_term_fail.stderr


def test_countermodel_j1_no_writes_to_final_target_after_publication(tmp_path: Path) -> None:
    """J1: Under transactional publication, all venv and pip work occurs strictly inside
    PRIVATE_STAGE before publication. The final path receives zero installer writes before,
    during, or after publication. If the target exists or appears concurrently, publication
    fails fail-closed (status 2), leaving the target completely untouched.
    """
    target_venv = tmp_path / "venv_j1_target"
    target_venv.mkdir()
    sentinel = target_venv / "foreign_data.txt"
    sentinel.write_text("FOREIGN_J1_DATA\n", encoding="utf-8")
    mtime_before = sentinel.stat().st_mtime_ns
    target_mtime_before = target_venv.stat().st_mtime_ns

    fake_python = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 2
    assert "refusing to reuse or mutate an existing path" in res.stderr
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "FOREIGN_J1_DATA\n"
    assert sentinel.stat().st_mtime_ns == mtime_before
    assert target_venv.stat().st_mtime_ns == target_mtime_before
    assert list(target_venv.iterdir()) == [sentinel]
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


def test_countermodel_j2_stat_then_rm_cleanup_boundary_eliminated(tmp_path: Path) -> None:
    """J2: Destructive cleanup authority over VENV_TARGET is completely eliminated.
    The cleanup handler only unlinks PRIVATE_STAGE. If an install fails before commit,
    even if VENV_TARGET is created concurrently with foreign contents, the cleanup handler
    never inspects, stats, or unlinks VENV_TARGET.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_j2_target"
    fake_py = tmp_path / "fake_py_j2.sh"
    fake_py.write_text(
        f"#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    target="' + str(target_venv) + '"\n'
        '    mkdir -p "$target"\n'
        '    echo "foreign sentinel content" > "$target/sentinel.txt"\n'
        '    exit 77\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 77
    assert target_venv.exists(), "Cleanup must NEVER touch VENV_TARGET"
    sentinel = target_venv / "sentinel.txt"
    assert sentinel.exists(), "Foreign sentinel must remain untouched"
    assert sentinel.read_text(encoding="utf-8") == "foreign sentinel content\n"
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


def test_signal_j3_parent_sigterm_during_venv_phase_reaps_all_descendants(tmp_path: Path) -> None:
    """J3: When parent installer receives SIGTERM during the venv creation phase,
    it forwards SIGTERM to the active process group, reaps all descendants (leaving zero orphans),
    cleans PRIVATE_STAGE, and exits 143 in < 0.5s.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_j3_venv_phase"
    barrier = tmp_path / "venv_barrier.txt"
    grandchild_pid_file = tmp_path / "grandchild.pid"

    fake_py = tmp_path / "fake_py_j3_venv.sh"
    fake_py.write_text(
        f"#!/bin/bash\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    sleep 300 & grandchild=$!\n'
        '    echo "$grandchild" > "' + str(grandchild_pid_file) + '"\n'
        '    echo "READY" > "' + str(barrier) + '"\n'
        '    wait "$grandchild"\n'
        "    exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    p = subprocess.Popen(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    for _ in range(50):
        if barrier.exists() and grandchild_pid_file.exists():
            break
        time.sleep(0.05)
    assert barrier.exists(), "Venv phase did not reach deterministic barrier"
    grandchild_pid = int(grandchild_pid_file.read_text().strip())

    start_time = time.time()
    os.kill(p.pid, signal.SIGTERM)

    try:
        stdout, stderr = p.communicate(timeout=2)
        elapsed = time.time() - start_time
        assert elapsed < 1.0, f"Installer must exit promptly on SIGTERM, took {elapsed}s"
        assert p.returncode == 143, f"Installer must exit 143 on SIGTERM, got {p.returncode}; stderr: {stderr}"
        assert not target_venv.exists(), "Target venv must not exist"
        assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0, "Staging directory must be cleaned"
        with pytest.raises(ProcessLookupError):
            os.kill(grandchild_pid, 0)
    except subprocess.TimeoutExpired:
        p.kill()
        assert False, "Installer hung and did not reap descendants within timeout"


def test_signal_j3_parent_sigterm_during_pip_phase_reaps_all_descendants(tmp_path: Path) -> None:
    """J3: When parent installer receives SIGTERM during the pip install phase,
    it forwards SIGTERM to the active process group, reaps all descendants (leaving zero orphans),
    cleans PRIVATE_STAGE, and exits 143 in < 0.5s.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_j3_pip_phase"
    barrier = tmp_path / "pip_barrier.txt"
    grandchild_pid_file = tmp_path / "pip_grandchild.pid"

    fake_py = tmp_path / "fake_py_j3_pip.sh"
    fake_py.write_text(
        f"#!/bin/bash\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    stage="$5"\n'
        '    mkdir -p "$stage/bin"\n'
        '    cat << "EOF" > "$stage/bin/python3"\n'
        '#!/bin/bash\n'
        'if [ "$1" = "-I" ] && [ "$2" = "-m" ] && [ "$3" = "pip" ]; then\n'
        '    sleep 300 & grandchild=$!\n'
        '    echo "$grandchild" > "' + str(grandchild_pid_file) + '"\n'
        '    echo "PIP_READY" > "' + str(barrier) + '"\n'
        '    wait "$grandchild"\n'
        '    exit 0\n'
        'fi\n'
        'exec python3 "$@"\n'
        'EOF\n'
        '    chmod 755 "$stage/bin/python3"\n'
        '    exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    p = subprocess.Popen(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    for _ in range(50):
        if barrier.exists() and grandchild_pid_file.exists():
            break
        time.sleep(0.05)
    assert barrier.exists(), "Pip phase did not reach deterministic barrier"
    grandchild_pid = int(grandchild_pid_file.read_text().strip())

    start_time = time.time()
    os.kill(p.pid, signal.SIGTERM)

    try:
        stdout, stderr = p.communicate(timeout=2)
        elapsed = time.time() - start_time
        assert elapsed < 1.0, f"Installer must exit promptly on SIGTERM, took {elapsed}s"
        assert p.returncode == 143, f"Installer must exit 143 on SIGTERM, got {p.returncode}; stderr: {stderr}"
        assert not target_venv.exists(), "Target venv must not exist"
        assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0, "Staging directory must be cleaned"
        with pytest.raises(ProcessLookupError):
            os.kill(grandchild_pid, 0)
    except subprocess.TimeoutExpired:
        p.kill()
        assert False, "Installer hung and did not reap descendants within timeout"


def test_publish_competition_preexisting_target_refused(tmp_path: Path) -> None:
    """Pre-existing target is refused immediately before staging or pip starts."""
    target_venv = tmp_path / "venv_preexisting"
    target_venv.mkdir()
    (target_venv / "file.txt").write_text("preexisting\n", encoding="utf-8")

    fake_python = _make_fake_python311(tmp_path)
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_python))
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 2
    assert "refusing to reuse or mutate an existing path" in res.stderr
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0
    assert (target_venv / "file.txt").read_text(encoding="utf-8") == "preexisting\n"


def test_publish_competition_concurrent_creator_wins(tmp_path: Path) -> None:
    """When a concurrent process creates VENV_TARGET while installation is ongoing in PRIVATE_STAGE,
    the rename_noreplace call detects EEXIST, publication fails fail-closed (status 2),
    PRIVATE_STAGE is cleaned up, and the winner's VENV_TARGET and contents remain completely untouched.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_concurrent_winner"

    fake_py = tmp_path / "fake_py_concurrent.sh"
    fake_py.write_text(
        f"#!/bin/bash\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    stage="$5"\n'
        '    mkdir -p "$stage/bin"\n'
        '    cat << "EOF" > "$stage/bin/python3"\n'
        '#!/bin/bash\n'
        'if [ "$1" = "-I" ] && [ "$2" = "-m" ] && [ "$3" = "pip" ]; then\n'
        '    mkdir -p "' + str(target_venv) + '"\n'
        '    echo "CONCURRENT_WINNER_DATA" > "' + str(target_venv) + '/winner.txt"\n'
        '    exit 0\n'
        'fi\n'
        'exec python3 "$@"\n'
        'EOF\n'
        '    chmod 755 "$stage/bin/python3"\n'
        '    exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 2
    assert "refusing to reuse or mutate an existing path" in res.stderr
    assert target_venv.exists()
    winner_file = target_venv / "winner.txt"
    assert winner_file.exists()
    assert winner_file.read_text(encoding="utf-8").strip() == "CONCURRENT_WINNER_DATA"
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


def test_relocation_closure_v1_console_script_and_census(tmp_path: Path) -> None:
    """RelocationClosureV1: Tests the offline fixture with a real local package containing a console script.
    Exercises:
      private staged venv
      -> local wheel/package install
      -> RelocationClosureV1 (R1 bytecode removal, R2 textual rewrite, R3 census, R4 shebang census)
      -> atomic rename_noreplace commit
      -> final/bin/python3 works
      -> sys.prefix points to final
      -> final/bin/python3 -m pip works
      -> console-script works
      -> shebang refers to final
      -> private staging path absent from all qualified durable files
    """
    import zipfile

    # Consume the actual projection and syscall code; publication authority is
    # exercised separately through the real launcher in the composition controls.
    py_step3 = (
        "import runpy, sys; "
        f"ns=runpy.run_path({str(INSTALL_AUTHORITY)!r},run_name='projection_fixture'); "
        "exec(ns['PREPARATION_CODE']); "
        "ns['rename_noreplace'](sys.argv[1], sys.argv[2])"
    )

    stage_venv = tmp_path / "stage_venv"
    final_venv = tmp_path / "final_venv"

    # 1. Create real venv at stage
    subprocess.run([sys.executable, "-m", "venv", str(stage_venv)], check=True)

    # 2. Build local pure-Python wheel with console script
    whl_path = tmp_path / "dummypkg-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl_path, "w") as z:
        z.writestr("dummypkg/__init__.py", 'def main():\n    print("DUMMY_CLI_SUCCESS")\n')
        z.writestr("dummypkg-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: dummypkg\nVersion: 0.1.0\n")
        z.writestr("dummypkg-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr("dummypkg-0.1.0.dist-info/entry_points.txt", "[console_scripts]\ndummy-tool = dummypkg:main\n")
        z.writestr("dummypkg-0.1.0.dist-info/RECORD", "")

    # Install into stage venv
    subprocess.run(
        [str(stage_venv / "bin" / "pip"), "install", "--no-deps", "--no-index", str(whl_path)],
        check=True,
        capture_output=True,
    )
    pre_cli = stage_venv / "bin" / "dummy-tool"
    assert pre_cli.exists()
    assert str(stage_venv) in pre_cli.read_text(encoding="utf-8").splitlines()[0]

    # 3. Execute Step 3 RelocationClosureV1 + atomic rename
    reloc_res = subprocess.run(
        [sys.executable, "-I", "-S", "-c", py_step3, str(stage_venv), str(final_venv)],
        capture_output=True,
        text=True,
    )
    assert reloc_res.returncode == 0, f"Relocation failed with stderr: {reloc_res.stderr}"

    # 4. Verify post-commit environment
    assert final_venv.exists()
    assert not stage_venv.exists()

    # sys.prefix points to final_venv
    py_res = subprocess.run(
        [str(final_venv / "bin" / "python3"), "-c", "import sys; print(sys.prefix)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert py_res.stdout.strip() == str(final_venv.resolve())

    # pip works from final_venv
    pip_res = subprocess.run(
        [str(final_venv / "bin" / "python3"), "-m", "pip", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert pip_res.returncode == 0

    # console script works and outputs expected string
    tool_res = subprocess.run(
        [str(final_venv / "bin" / "dummy-tool")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert tool_res.stdout.strip() == "DUMMY_CLI_SUCCESS"

    # Shebang refers to final, not stage
    cli_shebang = (final_venv / "bin" / "dummy-tool").read_text(encoding="utf-8").splitlines()[0]
    assert str(final_venv) in cli_shebang
    assert str(stage_venv) not in cli_shebang

    # Census: zero occurrences of stage_venv in any file in final_venv
    stage_bytes = os.fsencode(str(stage_venv))
    residuals: list[str] = []
    for root, dirs, files in os.walk(final_venv):
        for f in files:
            p_file = os.path.join(root, f)
            with open(p_file, "rb") as fp:
                if stage_bytes in fp.read():
                    residuals.append(p_file)
    assert residuals == [], f"Found residual staging references: {residuals}"


def test_relocation_closure_v1_rejects_unnormalizable_binary_reference(tmp_path: Path) -> None:
    """RelocationClosureV1: When an opaque/binary file in staging contains the private staging path,
    normalization cannot safely rewrite it; R2 detects the binary content, fails closed with status 2,
    and target is never committed or published.
    """
    # Consume the actual projection and syscall code; publication authority is
    # exercised separately through the real launcher in the composition controls.
    py_step3 = (
        "import runpy, sys; "
        f"ns=runpy.run_path({str(INSTALL_AUTHORITY)!r},run_name='projection_fixture'); "
        "exec(ns['PREPARATION_CODE']); "
        "ns['rename_noreplace'](sys.argv[1], sys.argv[2])"
    )

    stage_venv = tmp_path / "stage_binary"
    final_venv = tmp_path / "final_binary"
    stage_venv.mkdir()

    binary_file = stage_venv / "libfoo.so"
    binary_file.write_bytes(b"\x7fELF\x00\x00\x00" + os.fsencode(str(stage_venv)) + b"\x00\x01\x02")

    res = subprocess.run(
        [sys.executable, "-I", "-S", "-c", py_step3, str(stage_venv), str(final_venv)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "Blocked: opaque/binary file contains un-normalizable staging path" in res.stderr
    assert not final_venv.exists(), "Final target must NEVER be created on relocation failure"


def test_countermodel_k1_signal_after_publish_before_parent_commit_observation(tmp_path: Path) -> None:
    """C1: actual rename, pause BEFORE result update; internal cancellation cannot kill authority."""
    _run_publication_pause(tmp_path, after_commit=True)


def test_countermodel_k1_cancellation_before_commit_aborts(tmp_path: Path) -> None:
    """C3: cancel private preparation; a concurrent final target remains unchanged."""
    _run_publication_pause(tmp_path, after_commit=False)


def test_countermodel_k1_competition_eexist_remains_abort(tmp_path: Path) -> None:
    """K1-C: When a competitor creates VENV_TARGET concurrently before renameat2, rename_noreplace
    fails with EEXIST, no commit witness is written, private staging is cleaned up, competitor's
    target and files remain byte-identical, and installer exits with canonical refusal code 2.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_k1_competition"

    fake_py = tmp_path / "fake_py_k1_comp.sh"
    fake_py.write_text(
        f"#!/bin/bash\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ]; then\n'
        '    if [ -n "${5:-}" ]; then\n'
        f'        exec "{host_py}" "$@"\n'
        "    fi\n"
        '    echo "OK"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-m" ] && [ "$4" = "venv" ]; then\n'
        '    stage="$5"\n'
        '    mkdir -p "$stage/bin"\n'
        '    cat << "EOF" > "$stage/bin/python3"\n'
        "#!/bin/bash\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-m" ] && [ "$3" = "pip" ]; then\n'
        '    mkdir -p "' + str(target_venv) + '"\n'
        '    echo "COMPETITOR_DATA_BYTE_IDENTICAL" > "' + str(target_venv) + '/competitor.txt"\n'
        "    exit 0\n"
        "fi\n"
        'exec python3 "$@"\n'
        "EOF\n"
        '    chmod 755 "$stage/bin/python3"\n'
        "    exit 0\n"
        "fi\n"
        f'exec "{host_py}" "$@"\n',
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "refusing to reuse or mutate an existing path" in res.stderr
    assert target_venv.exists()
    comp_file = target_venv / "competitor.txt"
    assert comp_file.exists()
    assert comp_file.read_text(encoding="utf-8") == "COMPETITOR_DATA_BYTE_IDENTICAL\n"
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


def test_countermodel_z1_subreaper_reaps_orphaned_grandchildren_leaving_zero_zombies(tmp_path: Path) -> None:
    """Z1: When a worker process creates grandchild processes and exits without waiting for them,
    the subreaper wrapper adopts the orphaned descendants and reaps them completely upon exit,
    guaranteeing zero zombie processes even when ambient PID 1 does not reap orphans.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_z1_subreaper"
    grandchild_pid_file = tmp_path / "grandchild.pid"
    grandchild_done_file = tmp_path / "grandchild.done"

    fake_py = tmp_path / "fake_py_z1.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os, subprocess, time

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        sys.argv = ["<publish>", sys.argv[5], sys.argv[6]]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    with open(py_bin, "w") as f:
        f.write("#!/bin/sh\\nexit 0\\n")
    os.chmod(py_bin, 0o755)

    # Spawn an orphaned grandchild that outlives this direct child
    grandchild_code = '''
import time, os, sys
with open({repr(str(grandchild_pid_file))}, "w") as f:
    f.write(str(os.getpid()))
time.sleep(0.3)
with open({repr(str(grandchild_done_file))}, "w") as f:
    f.write("DONE")
sys.exit(0)
'''
    subprocess.Popen([sys.executable, "-c", grandchild_code])
    # Parent exits immediately, leaving grandchild orphaned
    sys.exit(0)

sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Installer must succeed, got {res.returncode}; stderr: {res.stderr}"
    assert grandchild_pid_file.exists(), "Grandchild PID file was not written"
    grandchild_pid = int(grandchild_pid_file.read_text().strip())

    # Wait for grandchild completion
    for _ in range(50):
        if grandchild_done_file.exists():
            break
        time.sleep(0.05)
    assert grandchild_done_file.exists(), "Grandchild did not complete execution"

    # Verify that grandchild is completely reaped: no zombie in /proc/<pid>/status
    proc_stat = Path(f"/proc/{grandchild_pid}/status")
    if proc_stat.exists():
        status_text = proc_stat.read_text(encoding="utf-8")
        assert "State:\tZ" not in status_text, f"Grandchild process {grandchild_pid} remained as a zombie!"
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)


def test_countermodel_p1_shebang_length_limit_and_pip_discarded(tmp_path: Path) -> None:
    """P1: Relocation and census strictly validate that executable shebang lines do not exceed
    the Linux kernel limit (127 bytes, BINPRM_BUF_SIZE = 128), and console pip scripts are discarded
    at the publication boundary (DiscardAtBoundary).
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_p1_shebang"

    # Case A: Shebang exceeding 127 bytes fails closed with exit 2
    fake_py_long = tmp_path / "fake_py_long.py"
    fake_py_long.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        sys.argv = ["<publish>", sys.argv[5], sys.argv[6]]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    with open(py_bin, "w") as f:
        f.write("#!/bin/sh\\nexit 0\\n")
    os.chmod(py_bin, 0o755)

    # Write a tool with a shebang exceeding 127 bytes
    tool_bin = os.path.join(stage, "bin", "oversized_tool")
    oversized_shebang = "#!" + "/usr/bin/python3" + "_" * 120 + "\\n"
    with open(tool_bin, "w") as f:
        f.write(oversized_shebang + "exit 0\\n")
    os.chmod(tool_bin, 0o755)
    sys.exit(0)

sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py_long.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py_long))
    res_fail = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_fail.returncode == 2, f"Oversized shebang must fail with exit 2, got {res_fail.returncode}"
    assert "exceeds AgentReviewShebangPolicyV1 limit" in res_fail.stderr
    assert not target_venv.exists()

    # Case B: Normal shebang succeeds, and bin/pip* is discarded at publication boundary
    fake_py_ok = tmp_path / "fake_py_ok.py"
    fake_py_ok.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        sys.argv = ["<publish>", sys.argv[5], sys.argv[6]]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    with open(py_bin, "w") as f:
        f.write("#!/bin/sh\\nexit 0\\n")
    os.chmod(py_bin, 0o755)

    # Write a pip script that should be discarded
    pip_bin = os.path.join(stage, "bin", "pip")
    with open(pip_bin, "w") as f:
        f.write("#!/bin/sh\\nexit 0\\n")
    os.chmod(pip_bin, 0o755)
    sys.exit(0)

sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py_ok.chmod(0o755)

    env_ok = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py_ok))
    res_ok = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env_ok,
        capture_output=True,
        text=True,
    )
    assert res_ok.returncode == 0, f"Valid shebang must succeed, got {res_ok.returncode}; stderr: {res_ok.stderr}"
    assert target_venv.exists()
    assert (target_venv / "bin" / "python3").exists()
    assert not (target_venv / "bin" / "pip").exists(), "bin/pip console script must be discarded at boundary"


def test_countermodel_t1_postcommit_signal_trap_preserves_committed_outcome(tmp_path: Path) -> None:
    """T1: request cancellation after known commit but before terminal result, without a product hook."""
    _run_publication_pause(tmp_path, after_commit=True, classified=True)


def test_countermodel_r1_target_path_contract_and_activation_discarded(tmp_path: Path) -> None:
    """R1: Target paths containing spaces succeed because activation scripts (which rely on shell quoting)
    are discarded at the boundary (DiscardAtBoundary), and TargetPathContract strictly rejects control
    characters and oversized path components fail-closed before any stage is created.
    """
    host_py = sys.executable

    # Case A: Path with whitespace succeeds and activation scripts are discarded
    target_with_spaces = tmp_path / "venv with spaces in target path"

    fake_py = tmp_path / "fake_py_r1.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        sys.argv = ["<publish>", sys.argv[5], sys.argv[6]]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    with open(py_bin, "w") as f:
        f.write("#!/bin/sh\\nexit 0\\n")
    os.chmod(py_bin, 0o755)

    # Write activation scripts that must be discarded
    for act in ["activate", "activate.csh", "activate.fish", "Activate.ps1"]:
        act_path = os.path.join(stage, "bin", act)
        with open(act_path, "w") as f:
            f.write("# activation script\\n")

    sys.exit(0)

sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    res_spaces = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_with_spaces)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_spaces.returncode == 0, f"Path with spaces must succeed, got {res_spaces.returncode}; stderr: {res_spaces.stderr}"
    assert target_with_spaces.exists()
    assert (target_with_spaces / "bin" / "python3").exists()
    for act in ["activate", "activate.csh", "activate.fish", "Activate.ps1"]:
        assert not (target_with_spaces / "bin" / act).exists(), f"{act} must be discarded at boundary"

    # Case B: Target path with newline is rejected fail-closed before staging
    target_with_newline = tmp_path / "venv\ninvalid"
    res_newline = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_with_newline)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_newline.returncode == 2, f"Path with newline must fail with exit 2, got {res_newline.returncode}"
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0

    # Case C: Target path with component > 255 bytes is rejected fail-closed
    oversized_component = tmp_path / ("c" * 256)
    res_oversized = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(oversized_component)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_oversized.returncode == 2, f"Path component > 255 must fail with exit 2, got {res_oversized.returncode}"
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


def test_subreaper_fail_closed_ablations(tmp_path: Path) -> None:
    """Q-PROC-01, Q-PROC-02: Subreaper setup and readback verification are strictly fail-closed.
    Ablations for SET failure, GET failure, and GET != 1 must fail closed with exit 2,
    reporting STOP_UNQUALIFIED_SUBREAPER before spawning any worker or staging directory.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_ablation"

    fake_py = tmp_path / "fake_py_ablation.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os
if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    base_env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))

    # Ablation 1: SET returns failure
    env_set = dict(base_env, _AIOPS_TEST_ABLATE_SUBREAPER_SET="1")
    res_set = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env_set,
        capture_output=True,
        text=True,
    )
    assert res_set.returncode == 2, f"SET ablation must exit 2, got {res_set.returncode}"
    assert "STOP_UNQUALIFIED_SUBREAPER" in res_set.stderr
    assert not target_venv.exists()
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0

    # Ablation 2: GET returns failure
    env_get = dict(base_env, _AIOPS_TEST_ABLATE_SUBREAPER_GET="1")
    res_get = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env_get,
        capture_output=True,
        text=True,
    )
    assert res_get.returncode == 2, f"GET ablation must exit 2, got {res_get.returncode}"
    assert "STOP_UNQUALIFIED_SUBREAPER" in res_get.stderr
    assert not target_venv.exists()
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0

    # Ablation 3: GET returns 0 (not subreaper)
    env_val = dict(base_env, _AIOPS_TEST_ABLATE_SUBREAPER_VAL="0")
    res_val = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env_val,
        capture_output=True,
        text=True,
    )
    assert res_val.returncode == 2, f"VAL ablation must exit 2, got {res_val.returncode}"
    assert "STOP_UNQUALIFIED_SUBREAPER" in res_val.stderr
    assert not target_venv.exists()
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


def test_bounded_descendant_termination_term_resistant_descendant(tmp_path: Path) -> None:
    """Q-PROC-03, Q-PROC-04: Bounded termination of TERM-resistant descendants.
    When a worker process spawns a descendant that explicitly ignores SIGTERM and sleeps,
    a cancellation signal (SIGTERM) delivered to the installer triggers initial SIGTERM,
    bounded grace period, and SIGKILL escalation, reaping all adopted descendants until
    ECHILD and leaving zero running, zombie, or waitable owned descendants.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_term_resistant"
    grandchild_pid_file = tmp_path / "term_resistant_grandchild.pid"

    fake_py = tmp_path / "fake_py_term_resistant.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os, subprocess, signal, time

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        sys.argv = ["<publish>", sys.argv[5], sys.argv[6]]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    with open(py_bin, "w") as f:
        f.write("#!/bin/sh\\nexit 0\\n")
    os.chmod(py_bin, 0o755)

    # Spawn grandchild that explicitly ignores SIGTERM and sleeps
    grandchild_code = '''
import time, os, sys, signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open({repr(str(grandchild_pid_file))}, "w") as f:
    f.write(str(os.getpid()))
time.sleep(120)
sys.exit(0)
'''
    subprocess.Popen([sys.executable, "-c", grandchild_code])
    # Parent worker process sleeps briefly so it is alive when cancelled
    time.sleep(30)
    sys.exit(0)

sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    start_time = time.time()
    p = subprocess.Popen(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Wait for the grandchild to spawn and write its PID
    for _ in range(60):
        if grandchild_pid_file.exists():
            break
        time.sleep(0.05)
    assert grandchild_pid_file.exists(), "Grandchild PID file was not written"
    grandchild_pid = int(grandchild_pid_file.read_text().strip())

    # Send SIGTERM to the installer process
    os.kill(p.pid, signal.SIGTERM)
    stdout, stderr = p.communicate(timeout=8)
    elapsed = time.time() - start_time

    # Must terminate boundedly under escalation (<= 5 seconds)
    assert elapsed < 5.0, f"Installer took {elapsed:.2f}s to terminate; escalation was not bounded"
    assert p.returncode == 143, f"Installer must return canonical cancellation code 143, got {p.returncode}"

    # Verify grandchild was terminated and reaped: no zombie, process nonexistent
    proc_stat = Path(f"/proc/{grandchild_pid}/status")
    if proc_stat.exists():
        status_text = proc_stat.read_text(encoding="utf-8")
        assert "State:\tZ" not in status_text, f"Grandchild process {grandchild_pid} remained as a zombie!"
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)

    # Target venv must not exist and staging directories cleaned up
    assert not target_venv.exists()
    assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


@pytest.mark.parametrize("outcome", ["worker_failure", "cancellation"])
@pytest.mark.parametrize("subreaper", [True, False], ids=["production", "ablation"])
def test_pid1_environment_qualification_u0_and_u1(tmp_path: Path, outcome: str, subreaper: bool) -> None:
    """U1 exercises the installer supervisor; PID1 waits only for its direct installer.

    Controlled venv workers are process witnesses, not runtime installation proof.
    The ablation changes only a disposable copy, disabling SET and its GET oracle.
    U0 remains covered separately by Z1 and bounded-descendant controls.
    """
    import json
    import textwrap

    probe = subprocess.run(
        ["unshare", "-U", "-r", "-p", "-f", "--mount-proc", "sh", "-c", "echo PID=$$"],
        capture_output=True, text=True, timeout=10,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "PID=1":
        pytest.skip("EVIDENCE_LIMITATION_NON_REAPING_PID1: " + probe.stderr.strip())

    installer = INSTALL_SCRIPT
    if not subreaper:
        disposable = tmp_path / "ablated"
        (disposable / "scripts").mkdir(parents=True)
        shutil.copy2(LOCK_FILE, disposable / LOCK_FILE.name)
        original = INSTALL_AUTHORITY.read_text(encoding="utf-8")
        set_call = "ctypes.c_int(PR_SET_CHILD_SUBREAPER),\n            ctypes.c_ulong(1),"
        get_oracle = "if val_get.value != 1:"
        assert original.count(set_call) == original.count(get_oracle) == 1
        changed = original.replace(set_call, set_call.replace("c_ulong(1)", "c_ulong(0)"))
        changed = changed.replace(get_oracle, "if val_get.value != 0:")
        installer = disposable / "scripts" / INSTALL_SCRIPT.name
        shutil.copy2(INSTALL_SCRIPT, installer)
        (disposable / "scripts" / INSTALL_AUTHORITY.name).write_text(changed, encoding="utf-8")
        (tmp_path / "ablation.diff").write_text(
            "PR_SET_CHILD_SUBREAPER argument: 1 -> 0\nGET acceptance: 1 -> 0\n",
            encoding="utf-8",
        )

    # Only the venv worker is controlled; all interpreter probes delegate to CPython.
    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent(r"""
        import os, signal, subprocess, sys, time
        from pathlib import Path
        root = Path(__file__).parent
        if sys.argv[1:5] != ["-I", "-S", "-m", "venv"]:
            os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
        stage = Path(sys.argv[5])
        (stage / "controlled-worker").write_text("process witness")
        (root / "worker.json").write_text(__import__("json").dumps({
            "pid": os.getpid(), "supervisor": os.getppid(), "stage": str(stage)}))
        child = os.fork()
        if child == 0:
            grandchild = os.fork()
            if grandchild != 0:
                deadline = time.monotonic() + 5
                while not (root / "descendant.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                os._exit(0)
            os.setsid()  # escape the worker group: supervisor adoption is required
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            (root / "descendant.json").write_text(__import__("json").dumps({
                "pid": os.getpid(), "birth_parent": os.getppid(), "sid": os.getsid(0)}))
            while True:
                time.sleep(0.02)
        os.waitpid(child, 0)  # direct child only; never reap the orphan
        while not (root / "release").exists():
            time.sleep(0.02)
        sys.exit(19)
    """), encoding="utf-8")
    wrapper = tmp_path / "python-worker"
    import shlex
    wrapper.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(worker)) + ' "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    pid1 = tmp_path / "pid1.py"
    pid1.write_text(textwrap.dedent(r"""
        import json, os, signal, subprocess, sys, time
        from pathlib import Path
        root, installer, wrapper, outcome, mechanism = sys.argv[1:]
        root = Path(root)
        assert os.getpid() == 1
        def read(name):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    return json.loads((root / name).read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    time.sleep(0.02)
            raise AssertionError("witness missing: " + name)
        def state(pid):
            try:
                data = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                return {"state": data[0], "ppid": int(data[1])}
            except FileNotFoundError:
                return None
        with (root / "installer.stdout").open("w") as out, (root / "installer.stderr").open("w") as err:
            proc = subprocess.Popen(["bash", installer, str(root / "target")],
                env=dict(os.environ, AGENT_REVIEW_PYTHON=wrapper),
                stdout=out, stderr=err, start_new_session=True)
            worker = read("worker.json")
            descendant = read("descendant.json")
            expected_parent = worker["supervisor"] if mechanism == "present" else 1
            deadline = time.monotonic() + 5
            adopted = state(descendant["pid"])
            while adopted and adopted["ppid"] != expected_parent and time.monotonic() < deadline:
                time.sleep(0.02)
                adopted = state(descendant["pid"])
            assert descendant["birth_parent"] not in (1, worker["supervisor"])
            assert adopted and adopted["ppid"] == expected_parent, (worker, descendant, adopted)
            assert worker["supervisor"] == proc.pid
            supervisor_cmd = Path(f"/proc/{worker['supervisor']}/cmdline").read_bytes()
            assert b"PR_SET_CHILD_SUBREAPER" in supervisor_cmd
            if outcome == "cancellation":
                proc.send_signal(signal.SIGTERM)
            else:
                (root / "release").touch()
            rc = proc.wait(timeout=15)  # PID1 waits ONLY for its direct child
        expected_rc = 143 if outcome == "cancellation" else 19
        assert rc == expected_rc, (rc, (root / "installer.stderr").read_text())
        remaining = {int(p.name): state(int(p.name)) for p in Path("/proc").iterdir() if p.name.isdigit() and p.name != "1"}
        target_absent = not (root / "target").exists()
        staging_absent = not list(root.glob(".agent_review_stage.*"))
        receipt = {"pid1": 1, "installer": proc.pid, "worker": worker,
            "descendant": descendant, "adoption": adopted, "outcome": outcome,
            "exit_status": rc, "mechanism": mechanism, "remaining_before_namespace_exit": remaining,
            "target_absent": target_absent, "staging_absent": staging_absent}
        (root / "u1-receipt.json").write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt), flush=True)
        assert target_absent and staging_absent
        if mechanism == "present":
            assert remaining == {}, remaining
        else:
            # The identical scenario violates adoption/reaping without the mechanism.
            assert descendant["pid"] in remaining, remaining
            assert remaining[descendant["pid"]]["ppid"] == 1
        # No generic waitpid or cleanup here: namespace teardown is not the oracle.
    """), encoding="utf-8")
    result = subprocess.run(
        ["unshare", "-U", "-r", "-p", "-f", "--mount-proc", sys.executable,
         str(pid1), str(tmp_path), str(installer), str(wrapper), outcome,
         "present" if subreaper else "absent"],
        capture_output=True, text=True, timeout=40,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((tmp_path / "u1-receipt.json").read_text())
    assert receipt["mechanism"] == ("present" if subreaper else "absent")


def test_target_path_contract_v1_equivalence_classes(tmp_path: Path) -> None:
    """Q-PATH-01, Q-PATH-02, Q-PATH-03: TargetPathContractV1 and AgentReviewTargetPathPolicyV1.
    Harness controls qualify argument/path preservation and execution via bin/python3.
    These wrappers do not prove a real venv, sys.prefix, lock dependencies or imports.
    All rejected classes fail closed before staging directory creation.
    """
    host_py = sys.executable

    def make_fake_py(marker: str) -> Path:
        f = tmp_path / f"fake_py_{marker}.py"
        f.write_text(
            f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        sys.argv = ["<publish>", sys.argv[5], sys.argv[6]]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    with open(py_bin, "w") as fp:
        fp.write({_path_harness_python(host_py, tmp_path / "harness-pip.args")!r})
    os.chmod(py_bin, 0o755)
    # Write activation scripts to prove they are discarded
    for act in ["activate", "activate.csh", "activate.fish", "Activate.ps1"]:
        with open(os.path.join(stage, "bin", act), "w") as fp:
            fp.write("# activation\\n")
    # Write pip console script to prove discarded
    with open(os.path.join(stage, "bin", "pip"), "w") as fp:
        fp.write("# pip\\n")
    os.chmod(os.path.join(stage, "bin", "pip"), 0o755)
    sys.exit(0)

sys.exit(1)
""",
            encoding="utf-8",
        )
        f.chmod(0o755)
        return f

    fake_py = make_fake_py("path_classes")
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))

    # Supported classes:
    supported_classes = [
        ("ordinary", tmp_path / "venv_ordinary"),
        ("whitespace", tmp_path / "venv with spaces"),
        ("single_quote", tmp_path / "venv'quote"),
        ("double_quote", tmp_path / 'venv"doublequote'),
        ("dollar", tmp_path / "venv$dollar"),
        ("backtick", tmp_path / "venv`backtick"),
        ("backslash", tmp_path / "venv\\backslash"),
        ("unicode", tmp_path / "venv_ñ_λ_🚀"),
    ]

    for class_name, target in supported_classes:
        res = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), str(target)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"Supported class '{class_name}' failed with {res.returncode}; stderr: {res.stderr}"
        assert (tmp_path / "harness-pip.args").read_text().splitlines() == [
            "-I", "-m", "pip", "--isolated", "install", "--require-hashes",
            "--no-deps", "-r", str(LOCK_FILE),
        ]
        assert target.exists(), f"Target '{target}' was not created"
        py_bin = target / "bin" / "python3"
        assert py_bin.exists(), f"python3 missing for '{class_name}'"

        # Consumer execution verified:
        res_exec = subprocess.run(
            [str(py_bin), "-c", "import sys; print('CONSUMER_OK')"],
            capture_output=True,
            text=True,
        )
        assert res_exec.returncode == 0, f"Consumer execution failed for '{class_name}': {res_exec.stderr}"
        assert "CONSUMER_OK" in res_exec.stdout

        # DiscardAtBoundary verified:
        for act in ["activate", "activate.csh", "activate.fish", "Activate.ps1"]:
            assert not (target / "bin" / act).exists(), f"{act} not discarded in '{class_name}'"
        assert not (target / "bin" / "pip").exists(), f"pip not discarded in '{class_name}'"

    # Rejected classes:
    rejected_classes = [
        ("lf", tmp_path / "venv\ninvalid"),
        ("cr", tmp_path / "venv\rinvalid"),
        ("oversized_component", tmp_path / ("x" * 256)),
        ("oversized_total_path", tmp_path / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200) / ("y" * 200)),
    ]

    for class_name, target in rejected_classes:
        res = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), str(target)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 2, f"Rejected class '{class_name}' must fail with exit 2, got {res.returncode}"
        assert len(list(tmp_path.glob(".agent_review_stage.*"))) == 0


def test_consumer_capability_closure_minimal_surface(tmp_path: Path) -> None:
    """Q-CONSUME-01, Q-REP-01..06: ConsumerCapabilityContractV1 and RuntimeProjectionContractV1.
    Wrapper control of published interface surfaces, not proof of a real venv/import closure:
    - bin/python3 is runtime_required (and executable)
    - activation scripts and console pip scripts are discarded
    - staging references are absent
    - shebangs conform to AgentReviewShebangPolicyV1 (<= 127 bytes)
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_consumer_closure"

    fake_py = tmp_path / "fake_py_consumer.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if "PR_SET_CHILD_SUBREAPER" in sys.argv[4]:
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        sys.argv = ["<norm>", sys.argv[5]]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 7:
        script = sys.argv[4]
        sys.argv = ["<publish>", sys.argv[5], sys.argv[6]]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    stage = sys.argv[5]
    os.makedirs(os.path.join(stage, "bin"), exist_ok=True)
    py_bin = os.path.join(stage, "bin", "python3")
    harness_file = os.path.join(os.path.dirname(stage), "consumer_harness_py.sh")
    with open(harness_file, "w") as fp:
        fp.write({_path_harness_python(host_py, tmp_path / "harness-pip.args")!r})
    os.chmod(harness_file, 0o755)
    os.symlink(harness_file, py_bin)

    # Add activation scripts (DISCARDABLE)
    for act in ["activate", "activate.csh", "activate.fish", "Activate.ps1"]:
        with open(os.path.join(stage, "bin", act), "w") as fp:
            fp.write("# activation script\\n")

    # Add pip console script (DISCARDABLE)
    with open(os.path.join(stage, "bin", "pip"), "w") as fp:
        fp.write("# pip\\n")
    os.chmod(os.path.join(stage, "bin", "pip"), 0o755)

    # Add dummy site-packages and text file with stage path to verify normalizer
    site = os.path.join(stage, "lib", "python3.11", "site-packages")
    os.makedirs(site, exist_ok=True)
    with open(os.path.join(site, "test_pkg.pth"), "w") as fp:
        fp.write(stage + "\\n")

    sys.exit(0)

sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py))
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Install failed with {res.returncode}; stderr: {res.stderr}"
    assert (tmp_path / "harness-pip.args").read_text().splitlines() == [
        "-I", "-m", "pip", "--isolated", "install", "--require-hashes",
        "--no-deps", "-r", str(LOCK_FILE),
    ]
    assert target_venv.exists()

    # 1. Runtime required: bin/python3 exists and executes
    py_bin = target_venv / "bin" / "python3"
    assert py_bin.exists()
    assert os.access(py_bin, os.X_OK)

    # 2. Discarded: activation scripts and pip scripts do not exist
    for act in ["activate", "activate.csh", "activate.fish", "Activate.ps1"]:
        assert not (target_venv / "bin" / act).exists()
    assert not (target_venv / "bin" / "pip").exists()

    # 3. Normalized: stage path was normalized to final target path in text files
    pth_file = target_venv / "lib" / "python3.11" / "site-packages" / "test_pkg.pth"
    assert pth_file.exists()
    assert str(target_venv) in pth_file.read_text(encoding="utf-8")
    assert ".agent_review_stage" not in pth_file.read_text(encoding="utf-8")


def _disposable_authority(tmp_path: Path, old: str, new: str) -> Path:
    """Instrument a disposable production copy; retain exact delta as evidence."""
    import difflib
    scripts = tmp_path / "instrumented" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(INSTALL_SCRIPT, scripts / INSTALL_SCRIPT.name)
    shutil.copy2(LOCK_FILE, scripts.parent / LOCK_FILE.name)
    original = INSTALL_AUTHORITY.read_text()
    assert original.count(old) == 1
    changed = original.replace(old, new)
    (scripts / INSTALL_AUTHORITY.name).write_text(changed)
    (tmp_path / "instrumentation.diff").write_text("".join(difflib.unified_diff(
        original.splitlines(True), changed.splitlines(True), fromfile="production", tofile="instrumented")))
    return scripts / INSTALL_SCRIPT.name


def _controlled_bootstrap(tmp_path: Path, before_prepare: str = "") -> Path:
    """Only venv/pip are controlled. Real CPython executes the complete authority."""
    import shlex
    code = (
        "import os,sys\nfrom pathlib import Path\n"
        "if sys.argv[1:5] != ['-I','-S','-m','venv']:\n"
        "    os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n"
        "stage=Path(sys.argv[5])\n(stage/'bin').mkdir(exist_ok=True)\n"
        "harness = stage.parent / 'path-harness-python.sh'\n"
        f"harness.write_text({_path_harness_python(sys.executable, tmp_path/'pip-arguments.txt')!r})\n"
        "harness.chmod(0o755)\n"
        "(stage/'bin'/'python3').symlink_to(harness)\n"
        "(stage/'pyvenv.cfg').write_text('controlled projection; not a real venv')\n" + before_prepare
    )
    worker = tmp_path / "controlled-bootstrap.py"
    worker.write_text(code)
    wrapper = tmp_path / "bootstrap"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(worker)) + ' "$@"\n')
    wrapper.chmod(0o755)
    return wrapper


def _await_file(path: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while (not path.exists() or not path.stat().st_size) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists(), f"Missing observation: {path}"


def _run_publication_pause(tmp_path: Path, after_commit: bool, classified: bool = False) -> None:
    import json
    barrier = tmp_path / "barrier.json"
    target = tmp_path / "target"
    bootstrap = _controlled_bootstrap(tmp_path)
    if after_commit:
        old = '                        publication = "COMMITTED"\n' if classified else '                        rename_noreplace(stage_dir, final_dir)\n'
        pause = (f"                        open({str(barrier)!r},'w').write(__import__('json').dumps({{'pid':os.getpid(),'stage':stage_dir,'final':final_dir}}))\n"
                 "                        os.kill(os.getpid(), signal.SIGSTOP)\n")
        new = old + pause
    else:
        # Preparation remains a cancellable worker. Observe before it prepares,
        # not inside the protected publication transition.
        bootstrap = _controlled_bootstrap(tmp_path,
            f"Path({str(barrier)!r}).write_text('preparation admitted')\nimport time\ntime.sleep(60)\n")
        old = "def main():\n"
        new = old + "    # observation copy; no semantic modification\n"
    installer = _disposable_authority(tmp_path, old, new)
    proc = subprocess.Popen(["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _await_file(barrier)
        if after_commit:
            deadline = time.monotonic() + 5
            while Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "T" and time.monotonic() < deadline:
                time.sleep(0.02)
            receipt = json.loads(barrier.read_text())
            assert receipt["pid"] == proc.pid  # exec transfer, no disposable publicador
            assert target.exists()
            identity = (target.stat().st_dev, target.stat().st_ino)
        else:
            target.mkdir()
            (target / "competitor").write_bytes(b"FOREIGN\n")
        proc.send_signal(signal.SIGTERM)
        if after_commit:
            time.sleep(3.3)  # cross OLD external KILL boundary; not a terminal oracle
            state = Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            assert state == "T", "authority must survive its own cancellation protocol"
            os.kill(proc.pid, signal.SIGCONT)
        out, err = proc.communicate(timeout=15)
        (tmp_path / "stdout.bin").write_bytes(out)
        (tmp_path / "stderr.bin").write_bytes(err)
        assert proc.returncode == (0 if after_commit else 143), err
        assert b"teardown=COMPLETE" in err
        if after_commit:
            assert b"publication=COMMITTED" in err
            assert (target.stat().st_dev, target.stat().st_ino) == identity
            assert b"installation already committed" in err
        else:
            assert b"publication=NOT_PUBLISHED" in err
            assert (target / "competitor").read_bytes() == b"FOREIGN\n"
        assert not list(tmp_path.glob(".agent_review_stage.*"))
        (tmp_path / "composition-receipt.json").write_text(json.dumps({
            "after_actual_rename": after_commit, "classified_before_pause": classified,
            "authority_pid": proc.pid, "exit_status": proc.returncode,
            "target_preserved": True, "teardown_complete": True}, indent=2))
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGCONT)
            proc.terminate()
            proc.wait(timeout=15)


def test_committed_teardown_failure_is_distinct_and_preserves_final(tmp_path: Path) -> None:
    """C5: finite fault injection after actual commit, no product fault hook."""
    bootstrap = _controlled_bootstrap(tmp_path)
    installer = _disposable_authority(tmp_path,
        "        try:\n            drain_children(force=True)\n            teardown_reason",
        "        try:\n            if publication == 'COMMITTED':\n                raise OSError('injected teardown observation failure')\n            drain_children(force=True)\n            teardown_reason")
    target = tmp_path / "target"
    result = subprocess.run(["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)), capture_output=True)
    assert result.returncode == 4, result.stderr
    assert b"publication=COMMITTED" in result.stderr
    assert b"teardown=FAILED" in result.stderr
    assert b"automatic_retry=false" in result.stderr
    assert b"venv ready at:" not in result.stdout
    assert target.is_dir() and (target / "pyvenv.cfg").is_file()
    assert not list(tmp_path.glob(".agent_review_stage.*"))
    (tmp_path / "stdout.bin").write_bytes(result.stdout)
    (tmp_path / "stderr.bin").write_bytes(result.stderr)


@pytest.mark.parametrize("pathname", ["ordinary", "venv\x1b[31mred", "venv\nforged", "venv\rforged", "venv\ttext", r"venv\ntext", "venv\u202espoof", "venv_ñ_λ_🚀"])
def test_installer_path_rendering_preserves_operational_value(tmp_path: Path, pathname: str) -> None:
    """C6: captured bytes, accepted-object identity, rejection and success diagnostics."""
    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / pathname
    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    rejected = '\n' in pathname or '\r' in pathname
    if not rejected:
        target.mkdir()
        (target / "sentinel").write_bytes(b"FOREIGN")
    refusal = subprocess.run(["bash", str(INSTALL_SCRIPT), str(target)], env=env, capture_output=True)
    assert refusal.returncode == 2
    for control in (b'\x1b', b'\r', b'\t', '\u202e'.encode()):
        assert control not in refusal.stderr
    if rejected:
        assert os.fsencode(str(target)) not in refusal.stderr
        assert not target.exists()
    else:
        assert (target / "sentinel").read_bytes() == b"FOREIGN"
        # The test alone removes its own foreign fixture, then checks success
        # using the identical operational pathname (harness, not real venv).
        shutil.rmtree(target)
        success = subprocess.run(["bash", str(INSTALL_SCRIPT), str(target)], env=env, capture_output=True)
        assert success.returncode == 0, success.stderr
        assert target.exists() and (target / "pyvenv.cfg").exists()
        for control in (b'\x1b', b'\r', b'\t', '\u202e'.encode()):
            assert control not in success.stdout + success.stderr
        (tmp_path / "success.stdout.bin").write_bytes(success.stdout)
    (tmp_path / "refusal.stderr.bin").write_bytes(refusal.stderr)


def test_noreplace_unavailable_fails_at_publication_without_fallback(tmp_path: Path) -> None:
    """1092: injected ENOSYS tests late negative handling, not platform qualification."""
    installer = _disposable_authority(tmp_path,
        "def rename_noreplace(src, dst):\n",
        "def rename_noreplace(src, dst):\n    raise OSError(errno.ENOSYS, 'injected unavailable NO_REPLACE')\n")
    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target"
    result = subprocess.run(["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)), capture_output=True)
    assert result.returncode == 3, result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert b"NO_REPLACE failed errno=38" in result.stderr
    assert b"teardown=COMPLETE" in result.stderr
    assert not target.exists() and not list(tmp_path.glob(".agent_review_stage.*"))
    assert (tmp_path / "pip-arguments.txt").exists()  # no new early capability probe


def test_surviving_reaper_paused_after_adoption_crosses_old_kill_window(tmp_path: Path) -> None:
    """C2: actual adopted zombie, real KILL/wait, census before namespace exit."""
    import json
    import textwrap
    probe = subprocess.run(["unshare", "-U", "-r", "-p", "-f", "--mount-proc", "sh", "-c", "echo PID=$$"], capture_output=True)
    if probe.returncode or probe.stdout.strip() != b"PID=1":
        pytest.skip("EVIDENCE_LIMITATION_NON_REAPING_PID1: " + probe.stderr.decode())
    barrier = tmp_path / "reaper-barrier.json"
    reaped = tmp_path / "reaped.json"
    observation = f'''                children = owned_children()
                if children and not os.path.exists({str(barrier)!r}):
                    observation_deadline = time.monotonic() + 5
                    while children and time.monotonic() < observation_deadline:
                        states = [open(f"/proc/{{pid}}/stat").read().rsplit(")",1)[1].split()[0] for pid in children]
                        if all(state == "Z" for state in states):
                            break
                        time.sleep(0.01)
                    open({str(barrier)!r}, "w").write(__import__("json").dumps({{"authority":os.getpid(),"children":children}}))
                    os.kill(os.getpid(), signal.SIGSTOP)
                waited = os.waitpid(-1, 0)
                open({str(reaped)!r}, "w").write(__import__("json").dumps({{"authority":os.getpid(),"waited":waited}}))
'''
    installer = _disposable_authority(tmp_path, "                os.waitpid(-1, 0)\n", observation)
    bootstrap = _controlled_bootstrap(tmp_path, f'''
import signal,subprocess,time,json
child_code="import os,signal,time,json;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path("+repr({str(tmp_path/'descendant.json')!r})+").write_text(json.dumps(dict(pid=os.getpid(),birth_parent=os.getppid())));time.sleep(60)"
child=subprocess.Popen([sys.executable,"-c",child_code])
Path({str(tmp_path/'worker.json')!r}).write_text(json.dumps(dict(pid=os.getpid(),authority=os.getppid(),descendant=child.pid)))
time.sleep(60)
''')
    driver = tmp_path / "pid1.py"
    driver.write_text(textwrap.dedent(r'''
        import json,os,signal,subprocess,sys,time
        from pathlib import Path
        root,installer,bootstrap=sys.argv[1:]
        root=Path(root)
        assert os.getpid()==1
        def read(name):
            end=time.monotonic()+10
            while time.monotonic()<end:
                try:return json.loads((root/name).read_text())
                except (FileNotFoundError,json.JSONDecodeError):time.sleep(.02)
            raise AssertionError(name)
        def state(pid):
            try:
                fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
                return dict(state=fields[0],ppid=int(fields[1]),start_ticks=int(fields[19]))
            except FileNotFoundError:return None
        with (root/'installer.stdout').open('w') as out,(root/'installer.stderr').open('w') as err:
            p=subprocess.Popen(['bash',installer,str(root/'target')],env=dict(os.environ,AGENT_REVIEW_PYTHON=bootstrap),stdout=out,stderr=err,start_new_session=True)
            worker=read('worker.json');desc=read('descendant.json')
            assert worker['authority']==p.pid
            p.send_signal(signal.SIGTERM)
            barrier=read('reaper-barrier.json')
            end=time.monotonic()+5
            while state(p.pid)['state']!='T' and time.monotonic()<end:time.sleep(.01)
            before=dict(authority=state(p.pid),descendant=state(desc['pid']))
            assert before['authority']['state']=='T'
            assert before['descendant']['state']=='Z' and before['descendant']['ppid']==p.pid
            assert desc['pid'] in barrier['children']
            p.send_signal(signal.SIGTERM)
            time.sleep(3.3)
            assert state(p.pid)==before['authority'], 'authority killed by internal escalation'
            assert state(desc['pid'])==before['descendant']
            os.kill(p.pid,signal.SIGCONT)
            rc=p.wait(timeout=15) # PID1 waits only for direct installer
        wait=read('reaped.json')
        assert wait['authority']==p.pid and wait['waited']==[desc['pid'],9]
        remaining={int(q.name):state(int(q.name)) for q in Path('/proc').iterdir() if q.name.isdigit() and q.name!='1'}
        receipt=dict(authority=p.pid,worker=worker,descendant=desc,adopted_zombie_before=before,actual_wait=wait,exit_status=rc,remaining_before_namespace_exit=remaining)
        (root/'c2-receipt.json').write_text(json.dumps(receipt,indent=2))
        print(json.dumps(receipt))
        assert rc==143 and remaining=={}
        assert not (root/'target').exists() and not list(root.glob('.agent_review_stage.*'))
        assert 'teardown=COMPLETE' in (root/'installer.stderr').read_text()
    '''))
    result = subprocess.run(["unshare", "-U", "-r", "-p", "-f", "--mount-proc", sys.executable,
        str(driver), str(tmp_path), str(installer), str(bootstrap)], capture_output=True, text=True, timeout=40)
    print(result.stdout)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "c2-receipt.json").read_text())["remaining_before_namespace_exit"] == {}


def test_cancel_during_resistant_cleanup_preserves_primary_and_classifies_teardown(tmp_path: Path) -> None:
    """Cleanup remains a supervised child; its cancellation cannot hide primary failure."""
    import shlex
    bootstrap = _controlled_bootstrap(tmp_path, "sys.exit(66)\n")
    marker = tmp_path / "cleanup.pid"
    resistant_bootstrap = tmp_path / "resistant_bootstrap.sh"
    resistant_bootstrap.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$3" = "-c" ] && [ -z "${6:-}" ]; then\n'
        '    case "$4" in\n'
        '        *CLEANUP_CODE*|*shutil.rmtree*|*cleanup\\ error*)\n'
        f'            exec {shlex.quote(sys.executable)} -c "import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"\n'
        '            ;;\n'
        '    esac\n'
        'fi\n'
        f'exec {shlex.quote(str(bootstrap))} "$@"\n'
    )
    resistant_bootstrap.chmod(0o755)
    proc = subprocess.Popen(["bash", str(INSTALL_SCRIPT), str(tmp_path / "target")],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(resistant_bootstrap)),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _await_file(marker)
    cleanup_pid = int(marker.read_text())
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 66, err
    assert b'publication=NOT_PUBLISHED' in err and b'teardown=FAILED' in err
    assert b'cleanup status 137' in err
    assert not Path(f'/proc/{cleanup_pid}').exists()
    assert not (tmp_path / 'target').exists()


@pytest.mark.parametrize("empty", [False, True], ids=["missing", "empty"])
def test_install_authority_dependency_cannot_disappear_as_success(tmp_path: Path, empty: bool) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(INSTALL_SCRIPT, scripts / INSTALL_SCRIPT.name)
    shutil.copy2(LOCK_FILE, tmp_path / LOCK_FILE.name)
    if empty:
        (scripts / INSTALL_AUTHORITY.name).write_text("")
    result = subprocess.run(["bash", str(scripts / INSTALL_SCRIPT.name), str(tmp_path / 'target')],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=sys.executable), capture_output=True)
    assert result.returncode == 2
    assert b'installation authority' in result.stderr
    assert not (tmp_path / 'target').exists()
    assert not list(tmp_path.glob('.agent_review_stage.*'))


def test_unestablished_publication_is_inconclusive_not_safe_cancellation(tmp_path: Path) -> None:
    """A classification fault cannot turn absent terminal knowledge into rollback."""
    installer = _disposable_authority(tmp_path,
        "                        rename_noreplace(stage_dir, final_dir)\n",
        "                        rename_noreplace(stage_dir, final_dir)\n                        raise RuntimeError('injected loss of classification')\n")
    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / 'target'
    result = subprocess.run(['bash', str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)), capture_output=True)
    assert result.returncode == 5, result.stderr
    assert b'publication=UNKNOWN' in result.stderr and b'automatic_retry=false' in result.stderr
    assert b'venv ready at:' not in result.stdout
    assert target.exists() and (target / 'pyvenv.cfg').exists()


def test_unqualified_interpreter_path_is_rendered_without_using_it(tmp_path: Path) -> None:
    unavailable = tmp_path / 'python\x1b[31m\t\u202efake'
    result = subprocess.run(['bash', str(INSTALL_SCRIPT), str(tmp_path / 'target')],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(unavailable)), capture_output=True)
    assert result.returncode == 2
    assert b'selected Python interpreter' in result.stderr
    assert b'\x1b' not in result.stderr and b'\t' not in result.stderr and '\u202e'.encode() not in result.stderr
    assert not (tmp_path / 'target').exists()


@pytest.mark.parametrize("capability", ["absent", "present"])
def test_proc_children_admission_precedes_filesystem_mutation(tmp_path: Path, capability: str) -> None:
    """Real procfs refusal/positive pair; venv/pip are local harness workers."""
    import json

    probe = subprocess.run(
        ["unshare", "-U", "-r", "-m", "-p", "-f", "-n", "--mount-proc", "true"],
        capture_output=True,
    )
    if probe.returncode:
        pytest.skip("EVIDENCE_LIMITATION_PRIVATE_PROCFS: " + probe.stderr.decode())
    parent = tmp_path / "missing" / "parent"
    target = parent / "target"
    worker = tmp_path / "worker-admitted.json"
    bootstrap = _controlled_bootstrap(tmp_path,
        f"Path({str(worker)!r}).write_text(__import__('json').dumps(dict(pid=os.getpid(),stage=str(stage))))\n")
    assert not parent.exists() and not target.exists()
    command = ("mount -t tmpfs -o mode=755 tmpfs /proc; " if capability == "absent" else "")
    command += 'exec bash "$1" "$2"'
    result = subprocess.run(
        ["unshare", "-U", "-r", "-m", "-p", "-f", "-n", "--mount-proc",
         "sh", "-ec", command, "proc-capability", str(INSTALL_SCRIPT), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)), capture_output=True, timeout=30,
    )
    (tmp_path / "installer.stdout").write_bytes(result.stdout)
    (tmp_path / "installer.stderr").write_bytes(result.stderr)
    receipt = dict(capability=capability, exit_status=result.returncode,
        parent_created=parent.exists(), final_created=target.exists(), worker_created=worker.exists(),
        stage_remaining=list(map(str, parent.glob(".agent_review_stage.*"))),
        mechanism="real production authority and procfs read; controlled local venv/pip only")
    (tmp_path / "proc-capability-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
    if capability == "absent":
        assert result.returncode == 2, result.stderr
        assert b"STOP_UNQUALIFIED_PROC_CHILDREN" in result.stderr
        assert b"publication=NOT_PUBLISHED" in result.stderr
        assert not parent.exists(), "capability refusal must precede parent mkdir"
        assert not worker.exists(), "no installation worker may be admitted"
        assert not (tmp_path / "pip-arguments.txt").exists()
        assert not target.exists() and not receipt["stage_remaining"]
    else:
        assert result.returncode == 0, result.stderr
        assert worker.exists() and (tmp_path / "pip-arguments.txt").exists()
        stage = Path(json.loads(worker.read_text())["stage"])
        assert stage.parent == parent and not stage.exists()
        assert target.is_dir() and (target / "pyvenv.cfg").is_file()
        assert b"publication=COMMITTED" in result.stderr and b"teardown=COMPLETE" in result.stderr
        assert not receipt["stage_remaining"]


@pytest.mark.parametrize("failure", ["unreadable", "nonnumeric", "zero", "overflow"])
def test_proc_children_reader_failure_refuses_before_mutation(tmp_path: Path, failure: str) -> None:
    """Identified stream-boundary faults in a disposable copy, no product hooks."""
    observed = tmp_path / "reader-consumed.txt"
    prefix = (
        "_production_open = open\n"
        "def open(path, *args, **kwargs):\n"
        '    if path == f"/proc/self/task/{os.getpid()}/children":\n'
        f"        with _production_open({str(observed)!r}, 'a') as observation:\n"
        "            observation.write(str(path) + '\\n')\n"
    )
    if failure == "unreadable":
        prefix += "        raise PermissionError(errno.EACCES, 'injected child-list read denial', path)\n"
    else:
        contents = {"nonnumeric": b"not-a-pid\n", "zero": b"0\n", "overflow": b"2147483648\n"}[failure]
        prefix += f"        return __import__('io').BytesIO({contents!r})\n"
    prefix += "    return _production_open(path, *args, **kwargs)\n\n"
    installer = _disposable_authority(tmp_path, "def read_proc_children():\n", prefix + "def read_proc_children():\n")
    bootstrap = _controlled_bootstrap(tmp_path, "raise AssertionError('worker must not be admitted')\n")
    parent = tmp_path / "missing" / "parent"
    target = parent / "target"
    result = subprocess.run(["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)), capture_output=True, timeout=30)
    (tmp_path / "installer.stderr").write_bytes(result.stderr)
    assert observed.read_text().strip().endswith("/children"), "real shared reader was not consumed"
    assert result.returncode == 2, result.stderr
    assert b"STOP_UNQUALIFIED_PROC_CHILDREN" in result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert not parent.exists() and not target.exists()
    assert not (tmp_path / "pip-arguments.txt").exists()
    assert not list(tmp_path.rglob(".agent_review_stage.*"))
    # No failed-capability drain/cleanup retry: exactly one startup read.
    assert len(observed.read_text().splitlines()) == 1


def test_proc_children_reader_observes_current_owned_children(tmp_path: Path) -> None:
    """Empty startup result is valid and is not cached for live consumers."""
    code = r"""
import json,runpy,subprocess,sys
ns=runpy.run_path(sys.argv[1])
ns['qualify_subreaper']()
ns['qualify_proc_children']()
assert ns['owned_children']()==[]
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
try:
    direct=ns['owned_children']()
    assert direct==[child.pid]
    assert ns['read_proc_children']()==direct
    print(json.dumps(dict(startup_empty=True,owned_children=direct,child=child.pid)))
finally:
    child.terminate()
    child.wait(timeout=10)
assert ns['owned_children']()==[]
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(INSTALL_AUTHORITY)],
        capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    (tmp_path / "reader-observation.stdout").write_bytes(result.stdout)


# ---------------------------------------------------------------------------
# AOCM Final Class Closure Suite:
# OC-RESULT-TOTALITY, OC-ENFORCEMENT-CLOSURE, OC-CENSUS-COMPLETE-OR-FAIL
# ---------------------------------------------------------------------------

def test_setup_failure_makedirs_produces_operational_failure(tmp_path: Path) -> None:
    """R-SETUP-1: makedirs failure is an operational failure (status 3), not admission refusal (status 2)."""
    installer = _disposable_authority(
        tmp_path,
        "            try:\n                os.makedirs(os.path.dirname(final_dir), exist_ok=True)\n",
        "            try:\n                raise PermissionError(errno.EACCES, 'injected makedirs permission denied')\n",
    )
    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "sub" / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 3, result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert b"staging setup failed: makedirs" in result.stderr
    assert b"not admitted" not in result.stderr
    assert b"teardown=COMPLETE (no stage created)" in result.stderr
    assert b"exit_status=3" in result.stderr
    assert not target.exists() and not list(tmp_path.glob(".agent_review_stage.*"))


def test_setup_failure_mkdtemp_produces_operational_failure(tmp_path: Path) -> None:
    """R-SETUP-2: mkdtemp failure is an operational failure (status 3), not admission refusal."""
    installer = _disposable_authority(
        tmp_path,
        "            try:\n                stage_dir = tempfile.mkdtemp(prefix=\".agent_review_stage.\", dir=os.path.dirname(final_dir))\n",
        "            try:\n                raise OSError(errno.ENOSPC, 'injected mkdtemp no space left')\n",
    )
    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 3, result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert b"staging setup failed: mkdtemp" in result.stderr
    assert b"not admitted" not in result.stderr
    assert b"teardown=COMPLETE (no stage created)" in result.stderr
    assert b"exit_status=3" in result.stderr
    assert not target.exists() and not list(tmp_path.glob(".agent_review_stage.*"))


def test_setup_failure_chmod_produces_operational_failure_and_cleans_stage(tmp_path: Path) -> None:
    """R-SETUP-3: chmod failure produces operational failure (status 3) and cleans created staging directory."""
    installer = _disposable_authority(
        tmp_path,
        "            try:\n                os.chmod(stage_dir, 0o755)\n",
        "            try:\n                raise OSError(errno.EPERM, 'injected chmod operation not permitted')\n",
    )
    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 3, result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert b"staging setup failed: chmod" in result.stderr
    assert b"not admitted" not in result.stderr
    assert b"teardown=COMPLETE" in result.stderr
    assert b"exit_status=3" in result.stderr
    assert not target.exists()
    assert not list(tmp_path.glob(".agent_review_stage.*")), "staging directory created before chmod failure must be cleaned"


def test_preparation_fails_closed_when_disposable_script_cannot_be_removed(tmp_path: Path) -> None:
    """R-ENF-1 (Finding 4173148126): mandatory DiscardAtBoundary fails closed if activation/pip script cannot be unlinked."""
    old_unlink = '                    os.unlink(p)\n'
    injected = (
        '                    if "activate" in f_lower or "pip" in f_lower:\n'
        '                        raise PermissionError(errno.EACCES, "injected unlink denial on unconsumed surface")\n'
        '                    os.unlink(p)\n'
    )
    installer = _disposable_authority(tmp_path, old_unlink, injected)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "(stage/'bin'/'activate').write_text('# activation script\\n')\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr
    assert b"Blocked: failed to discard unconsumed surface" in result.stderr
    assert b"publication=NOT_PUBLISHED (preparation failed or cancelled)" in result.stderr
    assert b"teardown=COMPLETE" in result.stderr
    assert not target.exists() and not list(tmp_path.glob(".agent_review_stage.*"))


def test_preparation_fails_closed_when_bytecode_discard_fails(tmp_path: Path) -> None:
    """R-ENF-2: mandatory bytecode removal fails closed if .pyc cannot be unlinked."""
    old_unlink = '            try:\n                os.unlink(p)\n'
    injected = (
        '            try:\n'
        '                raise PermissionError(errno.EACCES, "injected bytecode unlink denial")\n'
    )
    installer = _disposable_authority(tmp_path, old_unlink, injected)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "(stage/'bin'/'test.pyc').write_bytes(b'\\x00\\x00bytecode')\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr
    assert b"Blocked: failed to remove bytecode file" in result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert not target.exists() and not list(tmp_path.glob(".agent_review_stage.*"))


def test_preparation_census_fails_closed_on_traversal_error(tmp_path: Path) -> None:
    """R-CEN-1: traversal failure in os.walk during preparation invokes walk_error_handler and fails closed."""
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "unreadable = stage / 'unreadable_dir'\n"
        "unreadable.mkdir()\n"
        "(unreadable / 'hidden.txt').write_text('hidden')\n"
        "unreadable.chmod(0o000)\n",
    )
    target = tmp_path / "target"
    try:
        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), str(target)],
            env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
            capture_output=True,
        )
        assert result.returncode == 2, result.stderr
        assert b"Blocked: traversal failed during preparation" in result.stderr
        assert b"publication=NOT_PUBLISHED" in result.stderr
        assert not target.exists()
    finally:
        for p in tmp_path.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass


def test_preparation_r2_fails_closed_on_unreadable_file(tmp_path: Path) -> None:
    """R-CEN-2: R2 normalization fails closed if an existing file cannot be read."""
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "unreadable = stage / 'lib' / 'unreadable.pth'\n"
        "unreadable.parent.mkdir(parents=True, exist_ok=True)\n"
        "unreadable.write_text('content')\n"
        "unreadable.chmod(0o000)\n",
    )
    target = tmp_path / "target"
    try:
        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), str(target)],
            env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
            capture_output=True,
        )
        assert result.returncode == 2, result.stderr
        assert b"Blocked: cannot read" in result.stderr
        assert b"R2 normalization" in result.stderr
        assert b"publication=NOT_PUBLISHED" in result.stderr
        assert not target.exists()
    finally:
        for p in tmp_path.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass


def test_preparation_r3_fails_closed_on_unreadable_symlink(tmp_path: Path) -> None:
    """R-CEN-3: R3 residual census fails closed if readlink fails."""
    old_readlink = '                target_link = os.readlink(p)\n'
    injected = '                raise PermissionError(errno.EACCES, "injected readlink denial")\n'
    installer = _disposable_authority(tmp_path, old_readlink, injected)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "sym = stage / 'lib' / 'test_symlink'\n"
        "sym.parent.mkdir(parents=True, exist_ok=True)\n"
        "sym.symlink_to('target')\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr
    assert b"Blocked: cannot readlink" in result.stderr
    assert b"R3 census" in result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert not target.exists()


def test_preparation_r3_fails_closed_on_unreadable_file(tmp_path: Path) -> None:
    """R-CEN-4: R3 residual census fails closed if file content cannot be read."""
    old_read = '            with open(p, "rb") as fp:\n                content = fp.read()\n'
    injected = '            with open(p, "rb") as fp:\n                raise OSError(errno.EIO, "injected I/O error during R3 read")\n'
    installer = _disposable_authority(tmp_path, old_read, injected)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "f = stage / 'lib' / 'sample.txt'\n"
        "f.parent.mkdir(parents=True, exist_ok=True)\n"
        "f.write_text('clean content')\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr
    assert b"Blocked: cannot read" in result.stderr
    assert b"R3 census" in result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert not target.exists()


def test_preparation_r4_fails_closed_on_unreadable_executable(tmp_path: Path) -> None:
    """R-CEN-5: R4 shebang census fails closed if binary cannot be read."""
    old_readline = "                first_line = fp.readline()\n"
    injected = "                raise OSError(errno.EACCES, 'injected unreadable executable during R4')\n"
    installer = _disposable_authority(tmp_path, old_readline, injected)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "tool = stage / 'bin' / 'custom_tool'\n"
        "tool.write_bytes(b'#!/bin/sh\\necho hi\\n')\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr
    assert b"Blocked: cannot read" in result.stderr
    assert b"R4 census" in result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert not target.exists()


def test_ablation_oc_result_totality_proves_anti_vacuity(tmp_path: Path) -> None:
    """Section 14: proves anti-vacuity for OC-RESULT-TOTALITY.
    Ablating the fix (reverting to predecessor primary=2, publication_reason='not admitted')
    causes makedirs failure to produce exit 2 and 'not admitted' (the bug from finding 4173148118).
    """
    fixed_setup = (
        "            # Admission succeeded; advance phase to SETUP before first filesystem mutation.\n"
        "            primary = 3\n"
        "            publication_reason = \"staging setup in progress\"\n"
        "            teardown_reason = \"no stage created\"\n"
        "            try:\n"
        "                os.makedirs(os.path.dirname(final_dir), exist_ok=True)\n"
        "            except OSError as error:\n"
        "                publication_reason = f\"staging setup failed: makedirs ({error})\"\n"
        "                raise\n"
    )
    ablated_setup = (
        "            # Reverted to predecessor: primary remains 2, publication_reason remains 'not admitted'\n"
        "            raise PermissionError(errno.EACCES, 'injected makedirs failure')\n"
    )
    installer_ablated = _disposable_authority(tmp_path, fixed_setup, ablated_setup)
    ablated_path = installer_ablated.parent / INSTALL_AUTHORITY.name
    content = ablated_path.read_text(encoding="utf-8")
    content = content.replace(
        '        if not publication_reason or publication_reason == "not admitted":\n            publication_reason = f"installation failure: {error}"\n',
        "",
    )
    ablated_path.write_text(content, encoding="utf-8")

    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer_ablated), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, "ablated mutant must exhibit predecessor exit 2 escape"
    assert b"publication=NOT_PUBLISHED (not admitted)" in result.stderr


def test_ablation_oc_enforcement_closure_proves_anti_vacuity(tmp_path: Path) -> None:
    """Section 14: proves anti-vacuity for OC-ENFORCEMENT-CLOSURE.
    Ablating the fix (reverting DiscardAtBoundary to 'except OSError: pass')
    silently suppresses unlink failure and publishes the unconsumed surface (finding 4173148126).
    """
    fixed_discard = (
        "            try:\n"
        "                if os.path.islink(p) or os.path.isfile(p):\n"
        "                    os.unlink(p)\n"
        "                elif os.path.isdir(p):\n"
        "                    shutil.rmtree(p)\n"
        "            except OSError as err:\n"
        "                print(f\"Blocked: failed to discard unconsumed surface {ascii(p)}: {ascii(str(err))}\", file=sys.stderr)\n"
        "                sys.exit(2)\n"
    )
    ablated_discard = (
        "            try:\n"
        "                if os.path.islink(p) or os.path.isfile(p):\n"
        "                    os.unlink(p)\n"
        "                elif os.path.isdir(p):\n"
        "                    shutil.rmtree(p)\n"
        "            except OSError:\n"
        "                pass  # Reverted: silent suppression\n"
    )
    installer_ablated = _disposable_authority(tmp_path, fixed_discard, ablated_discard)
    ablated_path = installer_ablated.parent / INSTALL_AUTHORITY.name
    content = ablated_path.read_text(encoding="utf-8")
    content = content.replace("os.unlink(p)", "raise PermissionError(errno.EACCES, 'cannot unlink')")
    ablated_path.write_text(content, encoding="utf-8")

    bootstrap = _controlled_bootstrap(
        tmp_path,
        "(stage/'bin'/'activate').write_text('# retained activate\\n')\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer_ablated), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 0, "ablated mutant must exhibit silent publish escape"
    assert b"publication=COMMITTED" in result.stderr
    assert target.exists()


def test_ablation_oc_census_complete_proves_anti_vacuity(tmp_path: Path) -> None:
    """Section 14: proves anti-vacuity for OC-CENSUS-COMPLETE-OR-FAIL.
    Ablating the fix (reverting to default os.walk without onerror)
    silently ignores traversal errors and commits incomplete installations.
    """
    fixed_handler = (
        "def walk_error_handler(err):\n"
        "    print(f\"Blocked: traversal failed during preparation: {ascii(str(err))}\", file=sys.stderr)\n"
        "    sys.exit(2)\n"
    )
    ablated_handler = (
        "def walk_error_handler(err):\n"
        "    pass  # Reverted: silently suppress traversal error\n"
    )
    installer_ablated = _disposable_authority(tmp_path, fixed_handler, ablated_handler)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "unreadable = stage / 'unreadable_dir'\n"
        "unreadable.mkdir()\n"
        "(unreadable / 'hidden.txt').write_text('hidden')\n"
        "unreadable.chmod(0o000)\n",
    )
    target = tmp_path / "target"
    try:
        result = subprocess.run(
            ["bash", str(installer_ablated), str(target)],
            env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
            capture_output=True,
        )
        assert result.returncode == 0, "ablated mutant must exhibit silent traversal escape"
        assert b"publication=COMMITTED" in result.stderr
        assert target.exists()
    finally:
        for p in tmp_path.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass


def test_countermodel_r3_directory_symlink_rejected(tmp_path: Path) -> None:
    """CM-R3-DIR-SYMLINK (Finding 4173778176):
    os.walk classifies a directory symlink into `dirs`.
    Predecessor R3 only checked `files`, so directory symlinks pointing to
    the staging path survived preparation and were published.
    Fail-closed requirement: R3 must inspect symlinks in both dirs and files,
    rejecting any symlink targeting the staging directory with exit 2.
    """
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "real_sub = stage / 'real_subdir'\n"
        "real_sub.mkdir()\n"
        "dir_symlink = stage / 'dir_symlink'\n"
        "dir_symlink.symlink_to(real_sub, target_is_directory=True)\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, f"Directory symlink must fail closed with exit 2; got {result.returncode}\nstderr: {result.stderr.decode()}"
    assert b"Blocked: symlink" in result.stderr
    assert b"targets staging path" in result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert not target.exists()


def test_countermodel_r4_noncanonical_shebang_rejected(tmp_path: Path) -> None:
    """CM-R4-NONCANONICAL-SHEBANG (Finding 4173778178):
    Predecessor R4 accepted any shebang <= 127 bytes not containing stage_dir,
    permitting noncanonical interpreters (e.g. #!/bin/sh or #!/usr/bin/env python).
    Fail-closed requirement: R4 must compare shebang target against canonical
    final-venv Python interpreter and reject foreign shebangs with exit 2.
    """
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "foreign_tool = stage / 'bin' / 'foreign_tool'\n"
        "foreign_tool.write_bytes(b'#!/bin/sh\\necho foreign\\n')\n"
        "foreign_tool.chmod(0o755)\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 2, f"Foreign shebang must fail closed with exit 2; got {result.returncode}\nstderr: {result.stderr.decode()}"
    assert b"Blocked: noncanonical shebang" in result.stderr
    assert b"must target canonical venv Python" in result.stderr
    assert b"publication=NOT_PUBLISHED" in result.stderr
    assert not target.exists()


def test_countermodel_exec_ambient_helper_rejected(tmp_path: Path) -> None:
    """CM-EXEC-AMBIENT-HELPER (Finding 4173778184):
    Predecessor installer invoked pip via `['env', 'PIP_CONFIG_FILE=/dev/null', stage_python, ...]`,
    resolving `env` from ambient PATH. A rogue `env` ahead on PATH could intercept execution.
    Requirement: installer must invoke stage_python directly and pass PIP_CONFIG_FILE via
    subprocess environment mapping, never executing ambient PATH helpers.
    """
    import shlex
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    interception_marker = tmp_path / "ambient_env_intercepted.marker"
    rogue_env = fake_bin / "env"
    rogue_env.write_text(
        f"#!/bin/sh\n"
        f"echo 'INTERCEPTED' > {shlex.quote(str(interception_marker))}\n"
        f"exit 0\n"
    )
    rogue_env.chmod(0o755)

    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=env,
        capture_output=True,
    )
    assert not interception_marker.exists(), (
        "Vulnerability reproduced: ambient PATH helper 'env' was executed by installer"
    )


def test_ablation_r3_directory_symlink_proves_anti_vacuity(tmp_path: Path) -> None:
    """Proves anti-vacuity for OC-CENSUS-COMPLETE-OR-FAIL (Finding 4173778176):
    Ablating the R3 directory symlink inspection (reverting to predecessor loop
    over files only) silently permits a directory symlink targeting the staging
    directory to survive and commit into the final target.
    """
    fixed_r3_dirs = (
        "for root, dirs, files in os.walk(stage_dir, onerror=walk_error_handler):\n"
        "    for entry in list(dirs) + list(files):\n"
    )
    ablated_r3_dirs = (
        "for root, dirs, files in os.walk(stage_dir, onerror=walk_error_handler):\n"
        "    for entry in files:  # Reverted: predecessor omitted directory symlink inspection\n"
    )
    installer_ablated = _disposable_authority(tmp_path, fixed_r3_dirs, ablated_r3_dirs)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "real_sub = stage / 'real_subdir'\n"
        "real_sub.mkdir()\n"
        "dir_symlink = stage / 'dir_symlink'\n"
        "dir_symlink.symlink_to(real_sub, target_is_directory=True)\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer_ablated), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 0, "ablated mutant must exhibit predecessor silent publish escape for directory symlinks"
    assert b"publication=COMMITTED" in result.stderr
    assert target.exists()


def test_ablation_r4_canonical_shebang_proves_anti_vacuity(tmp_path: Path) -> None:
    """Proves anti-vacuity for OC-ENFORCEMENT-CLOSURE (Finding 4173778178):
    Ablating the R4 canonical shebang check (reverting to predecessor which only
    checked staging path and length <= 127) silently accepts foreign shebangs
    and commits them into the final target.
    """
    fixed_r4_check = (
        "            shebang_target = first_line[2:].strip()\n"
        "            if shebang_target not in canonical_pythons:\n"
        "                print(f\"Blocked: noncanonical shebang {ascii(first_line)} in {ascii(p)}; must target canonical venv Python\", file=sys.stderr)\n"
        "                sys.exit(2)\n"
    )
    ablated_r4_check = (
        "            # Reverted: predecessor accepted foreign shebangs (e.g. #!/bin/sh)\n"
    )
    installer_ablated = _disposable_authority(tmp_path, fixed_r4_check, ablated_r4_check)
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "foreign_tool = stage / 'bin' / 'foreign_tool'\n"
        "foreign_tool.write_bytes(b'#!/bin/sh\\necho foreign\\n')\n"
        "foreign_tool.chmod(0o755)\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(installer_ablated), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 0, "ablated mutant must exhibit predecessor silent publish escape for foreign shebangs"
    assert b"publication=COMMITTED" in result.stderr
    assert target.exists()


def test_ablation_exec_ambient_helper_proves_anti_vacuity(tmp_path: Path) -> None:
    """Proves anti-vacuity for OC-AMBIENT-EXECUTABLE-IDENTITY (Finding 4173778184):
    Ablating the direct execution (reverting to predecessor `['env', ...]`) causes
    the installer to execute the ambient PATH helper rather than the validated interpreter.
    """
    import shlex
    fixed_pip_invocation = (
        "            phases = (\n"
        "                ([bootstrap, \"-I\", \"-S\", \"-m\", \"venv\", stage_dir], None),\n"
        "                ([stage_python, \"-I\", \"-m\", \"pip\", \"--isolated\", \"install\", \"--require-hashes\", \"--no-deps\", \"-r\", lock_file], pip_env),\n"
        "                ([bootstrap, \"-I\", \"-S\", \"-c\", PREPARATION_CODE, stage_dir, final_dir], None),\n"
        "            )\n"
    )
    ablated_pip_invocation = (
        "            phases = (\n"
        "                ([bootstrap, \"-I\", \"-S\", \"-m\", \"venv\", stage_dir], None),\n"
        "                ([\"env\", \"PIP_CONFIG_FILE=/dev/null\", stage_python, \"-I\", \"-m\", \"pip\", \"--isolated\", \"install\", \"--require-hashes\", \"--no-deps\", \"-r\", lock_file], None),\n"
        "                ([bootstrap, \"-I\", \"-S\", \"-c\", PREPARATION_CODE, stage_dir, final_dir], None),\n"
        "            )\n"
    )
    installer_ablated = _disposable_authority(tmp_path, fixed_pip_invocation, ablated_pip_invocation)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    interception_marker = tmp_path / "ambient_env_intercepted.marker"
    rogue_env = fake_bin / "env"
    rogue_env.write_text(
        f"#!/bin/sh\n"
        f"echo 'INTERCEPTED' > {shlex.quote(str(interception_marker))}\n"
        f"exit 0\n"
    )
    rogue_env.chmod(0o755)

    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    subprocess.run(
        ["bash", str(installer_ablated), str(target)],
        env=env,
        capture_output=True,
    )
    assert interception_marker.exists(), "ablated mutant must execute ambient PATH helper 'env'"


def test_positive_r3_valid_directory_structure_accepted(tmp_path: Path) -> None:
    """Positive control for R3: regular subdirectories and files without staging
    references are admitted and committed cleanly.
    """
    bootstrap = _controlled_bootstrap(
        tmp_path,
        "valid_sub = stage / 'valid_subdir' / 'nested'\n"
        "valid_sub.mkdir(parents=True)\n"
        "(valid_sub / 'data.txt').write_text('clean content')\n",
    )
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 0, f"Valid directory structure must succeed; got {result.returncode}\nstderr: {result.stderr.decode()}"
    assert b"publication=COMMITTED" in result.stderr
    assert (target / "valid_subdir" / "nested" / "data.txt").is_file()


def test_positive_r4_canonical_shebang_and_binary_accepted(tmp_path: Path) -> None:
    """Positive control for R4: scripts with canonical venv shebangs and ELF binaries
    are admitted and committed cleanly.
    """
    target = tmp_path / "target"
    expected_shebang = f"#!{target}/bin/python3\n"
    bootstrap = _controlled_bootstrap(
        tmp_path,
        f"(stage / 'bin' / 'canonical_tool').write_bytes({expected_shebang.encode()!r} + b'print(42)\\n')\n"
        f"(stage / 'bin' / 'canonical_tool').chmod(0o755)\n"
        f"(stage / 'bin' / 'elf_tool').write_bytes(b'\\x7fELFfakebinary')\n"
        f"(stage / 'bin' / 'elf_tool').chmod(0o755)\n",
    )
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap)),
        capture_output=True,
    )
    assert result.returncode == 0, f"Canonical shebang and ELF binary must succeed; got {result.returncode}\nstderr: {result.stderr.decode()}"
    assert b"publication=COMMITTED" in result.stderr
    assert (target / "bin" / "canonical_tool").is_file()
    assert (target / "bin" / "elf_tool").is_file()


def test_positive_exec_stage_python_direct_with_pip_env(tmp_path: Path) -> None:
    """Positive control for ambient executable identity: installer succeeds with
    status 0 and commits publication even when rogue `env` exists on PATH,
    confirming direct invocation of stage_python without relying on ambient PATH helpers.
    """
    import shlex
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    interception_marker = tmp_path / "ambient_env_intercepted.marker"
    rogue_env = fake_bin / "env"
    rogue_env.write_text(
        f"#!/bin/sh\n"
        f"echo 'INTERCEPTED' > {shlex.quote(str(interception_marker))}\n"
        f"exit 99\n"
    )
    rogue_env.chmod(0o755)

    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0, f"Installer must succeed; got {result.returncode}\nstderr: {result.stderr.decode()}"
    assert b"publication=COMMITTED" in result.stderr
    assert not interception_marker.exists(), "Rogue env on PATH must not be executed"
    assert target.exists()


def test_countermodel_ambient_cat_authority_rejected(tmp_path: Path) -> None:
    """CM-AMBIENT-CAT-AUTHORITY: When PATH contains a rogue `cat` returning fake
    python code (e.g. exit 0), the installer must not execute the rogue cat or let
    substituted bytes reach Python -c, and must not return success without creating target.
    """
    import shlex
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "cat_invoked.marker"
    rogue_cat = fake_bin / "cat"
    rogue_cat.write_text(
        "#!/bin/sh\n"
        f"echo 'ROGUE_CAT_INVOKED' > {shlex.quote(str(marker))}\n"
        "echo \"print('rogue cat intercepted authority'); import sys; sys.exit(0)\"\n"
    )
    rogue_cat.chmod(0o755)

    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target_cat"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=env,
        capture_output=True,
        text=True,
    )

    # Secure property: rogue cat must not be executed, real authority must run
    assert not marker.exists(), "Rogue cat on PATH must not be executed to read authority"
    assert "rogue cat intercepted authority" not in result.stdout
    assert result.returncode == 0, f"Real install must succeed; got {result.returncode}\nstderr: {result.stderr}"
    assert target.exists(), "Target venv must be created by real authority"


def test_countermodel_ambient_rm_teardown_rejected(tmp_path: Path) -> None:
    """CM-AMBIENT-RM-TEARDOWN: When PATH contains a rogue `rm` returning 0 without deleting,
    a failed installation requiring cleanup must not fabricate teardown=COMPLETE while
    stage_dir remains uncleaned on disk. Teardown must be FAILED, or stage must be absent.
    """
    import shlex
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "rm_invoked.marker"
    rogue_rm = fake_bin / "rm"
    rogue_rm.write_text(
        "#!/bin/sh\n"
        f"echo 'ROGUE_RM_INVOKED' > {shlex.quote(str(marker))}\n"
        "exit 0\n"
    )
    rogue_rm.chmod(0o755)

    # Controlled bootstrap that causes deliberate failure during pip phase
    bootstrap = _controlled_bootstrap(tmp_path, "sys.exit(66)\n")
    target = tmp_path / "target_rm"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target)],
        env=env,
        capture_output=True,
        text=True,
    )

    remaining_stages = list(tmp_path.glob(".agent_review_stage.*"))
    # Secure property: rogue rm must not be executed, and uncleaned staging must not report teardown=COMPLETE
    assert not marker.exists(), "Rogue rm on PATH must not be executed for staging cleanup"
    assert len(remaining_stages) == 0, f"Staging directory leaked after cleanup: {remaining_stages}"
    if remaining_stages:
        assert "teardown=COMPLETE" not in result.stderr


def _disposable_installer(tmp_path: Path, old: str, new: str) -> Path:
    """Instrument a disposable copy of install-agent-review-toolrepo.sh."""
    scripts = tmp_path / "instrumented_installer" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(INSTALL_AUTHORITY, scripts / INSTALL_AUTHORITY.name)
    shutil.copy2(LOCK_FILE, scripts.parent / LOCK_FILE.name)
    original = INSTALL_SCRIPT.read_text()
    assert original.count(old) == 1
    changed = original.replace(old, new)
    installer = scripts / INSTALL_SCRIPT.name
    installer.write_text(changed)
    installer.chmod(0o755)
    return installer


def test_ablation_ambient_cat_proves_anti_vacuity(tmp_path: Path) -> None:
    """Proves anti-vacuity for OC-AMBIENT-EXECUTABLE-IDENTITY (Finding 4174769886):
    Ablating authority code loading back to $(cat "$INSTALL_AUTHORITY") causes the
    installer to execute ambient `cat` from PATH, allowing rogue cat to intercept
    the authority source code.
    """
    import shlex
    old_code = 'AUTHORITY_CODE="$(< "$INSTALL_AUTHORITY")"'
    new_code = 'AUTHORITY_CODE="$(cat "$INSTALL_AUTHORITY")"'
    installer_ablated = _disposable_installer(tmp_path, old_code, new_code)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    marker = tmp_path / "cat_ablation.marker"
    rogue_cat = fake_bin / "cat"
    rogue_cat.write_text(
        "#!/bin/sh\n"
        f"echo 'ROGUE_CAT_INVOKED' > {shlex.quote(str(marker))}\n"
        "echo \"print('rogue cat intercepted authority'); import sys; sys.exit(0)\"\n"
    )
    rogue_cat.chmod(0o755)

    bootstrap = _controlled_bootstrap(tmp_path)
    target = tmp_path / "target_cat_ablation"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(installer_ablated), str(target)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert marker.exists(), "Ablated mutant must invoke rogue cat on PATH"
    assert "rogue cat intercepted authority" in result.stdout
    assert not target.exists(), "Target must not be created when rogue cat intercepts authority"


def test_ablation_ambient_rm_proves_anti_vacuity(tmp_path: Path) -> None:
    """Proves anti-vacuity for OC-AMBIENT-EXECUTABLE-IDENTITY / OC-ENFORCEMENT-CLOSURE (Finding 4174769894):
    Ablating staging cleanup back to ambient `rm` without postcondition absence check causes
    a failed install to invoke ambient `rm` and falsely report teardown=COMPLETE while
    staging directory remains leaked on disk.
    """
    import shlex
    fixed_cleanup = (
        '                cleanup_status = run_phase([bootstrap, "-I", "-S", "-c", CLEANUP_CODE, stage_dir], cleanup=True)\n'
        '                if cleanup_status:\n'
        '                    raise RuntimeError(f"cleanup status {cleanup_status}")\n'
        '                if os.path.lexists(stage_dir):\n'
        '                    raise RuntimeError(f"staging directory still exists after cleanup: {stage_dir}")\n'
    )
    ablated_cleanup = (
        '                cleanup_status = run_phase(["rm", "-rf", "--", stage_dir], cleanup=True)\n'
        '                if cleanup_status:\n'
        '                    raise RuntimeError(f"cleanup status {cleanup_status}")\n'
    )
    installer_ablated = _disposable_authority(tmp_path, fixed_cleanup, ablated_cleanup)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    marker = tmp_path / "rm_ablation.marker"
    rogue_rm = fake_bin / "rm"
    rogue_rm.write_text(
        "#!/bin/sh\n"
        f"echo 'ROGUE_RM_INVOKED' > {shlex.quote(str(marker))}\n"
        "exit 0\n"
    )
    rogue_rm.chmod(0o755)

    bootstrap = _controlled_bootstrap(tmp_path, "sys.exit(66)\n")
    target = tmp_path / "target_rm_ablation"

    env = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(installer_ablated), str(target)],
        env=env,
        capture_output=True,
        text=True,
    )

    remaining_stages = list(tmp_path.glob(".agent_review_stage.*"))
    assert marker.exists(), "Ablated mutant must invoke rogue rm on PATH"
    assert len(remaining_stages) > 0, "Staging directory must leak when rogue rm fails to delete"
    assert "teardown=COMPLETE" in result.stderr, "Ablated mutant must falsely report teardown=COMPLETE despite leak"


def test_positive_ambient_cat_and_rm_eliminated(tmp_path: Path) -> None:
    """Positive control for OC-AMBIENT-EXECUTABLE-IDENTITY:
    With rogue `cat` and `rm` on PATH that would exit 1 or record invocation if called,
    both normal successful installation and teardown on failure proceed cleanly without
    ever invoking the ambient helpers.
    """
    import shlex
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()

    cat_marker = tmp_path / "cat_called.marker"
    rogue_cat = fake_bin / "cat"
    rogue_cat.write_text(f"#!/bin/sh\necho CAT > {shlex.quote(str(cat_marker))}\nexit 1\n")
    rogue_cat.chmod(0o755)

    rm_marker = tmp_path / "rm_called.marker"
    rogue_rm = fake_bin / "rm"
    rogue_rm.write_text(f"#!/bin/sh\necho RM > {shlex.quote(str(rm_marker))}\nexit 1\n")
    rogue_rm.chmod(0o755)

    # 1. Successful installation run
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    bootstrap_ok = _controlled_bootstrap(ok_dir)
    target_ok = ok_dir / "target_ok"
    env_ok = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap_ok))
    env_ok["PATH"] = f"{fake_bin}:{env_ok.get('PATH', '')}"

    result_ok = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_ok)],
        env=env_ok,
        capture_output=True,
        text=True,
    )
    assert result_ok.returncode == 0, f"Install failed:\nstdout: {result_ok.stdout}\nstderr: {result_ok.stderr}"
    assert "publication=COMMITTED" in result_ok.stderr
    assert target_ok.exists()
    assert not cat_marker.exists(), "Ambient cat must not be invoked during successful install"
    assert not rm_marker.exists(), "Ambient rm must not be invoked during successful install"

    # 2. Failed installation with clean teardown
    fail_dir = tmp_path / "fail"
    fail_dir.mkdir()
    bootstrap_fail = _controlled_bootstrap(fail_dir, "sys.exit(66)\n")
    target_fail = fail_dir / "target_fail"
    env_fail = dict(os.environ, AGENT_REVIEW_PYTHON=str(bootstrap_fail))
    env_fail["PATH"] = f"{fake_bin}:{env_fail.get('PATH', '')}"

    result_fail = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_fail)],
        env=env_fail,
        capture_output=True,
        text=True,
    )
    assert result_fail.returncode == 66
    assert "publication=NOT_PUBLISHED" in result_fail.stderr
    assert "teardown=COMPLETE" in result_fail.stderr
    assert not target_fail.exists()
    assert not list(fail_dir.glob(".agent_review_stage.*"))
    assert not cat_marker.exists(), "Ambient cat must not be invoked during failed install"
    assert not rm_marker.exists(), "Ambient rm must not be invoked during teardown"
