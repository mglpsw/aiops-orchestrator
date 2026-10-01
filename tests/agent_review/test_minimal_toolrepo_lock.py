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
    # Exit code 42 proves the script passed the version check and proceeded to "$PYTHON_BIN -I -S -m venv <canonical_target>"
    assert result.returncode == 42
    assert "requirements-agent-review.lock is qualified for CPython 3.11" not in result.stderr
    assert f"GUARD_PASSED_CALLED_WITH: -I -S -m venv {str(target_venv.resolve())}" in result.stderr


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
    script_text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert 'PIP_CONFIG_FILE=/dev/null "$VENV_TARGET/bin/python3" -I -m pip --isolated install' in script_text, (
        "scripts/install-agent-review-toolrepo.sh must invoke pip with PIP_CONFIG_FILE=/dev/null and --isolated"
    )


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
    expected_canonical = str((tmp_path / "rel_venv").resolve())
    assert f"GUARD_PASSED_CALLED_WITH: -I -S -m venv {expected_canonical}" in result.stderr


@pytest.mark.requires_network
def test_install_script_produces_a_working_minimal_venv(tmp_path: Path) -> None:
    venv_dir = tmp_path / "agent-review-venv"
    env = os.environ.copy()
    if "AGENT_REVIEW_PYTHON" not in env:
        py311 = shutil.which("python3.11")
        if py311:
            env["AGENT_REVIEW_PYTHON"] = py311
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
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        script = sys.argv[4]
        private_dir = sys.argv[5]
        target = sys.argv[6]
        token_file = sys.argv[7]
        # Simulate concurrent actor winning the race before publication:
        os.mkdir(target)
        with open(os.path.join(target, "sentinel.txt"), "w") as f:
            f.write({repr(sentinel_text)})
        sys.argv = ["<claim>", private_dir, target, token_file]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    sys.exit(42)
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

    # 4. Target may remain because cleanup failed
    assert target_venv.exists()


def test_countermodel_g1_cleanup_skips_when_directory_object_identity_replaced(tmp_path: Path) -> None:
    """G1: Cleanup must remain bound to the claimed directory object identity (device:inode).

    If the claimed directory is replaced by a different directory object or symlink before cleanup runs,
    the cleanup handler must detect the identity mismatch, skip rm -rf, and preserve the original failure.
    """
    host_py = sys.executable

    # Case A: Claimed directory replaced by another directory object with different inode
    target_venv_a = tmp_path / "venv_g1_replaced_dir"
    fake_py_a = tmp_path / "fake_py_replace_dir.sh"
    fake_py_a.write_text(
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
        '    mv "$target" "${target}_old"\n'
        '    mkdir "$target"\n'
        '    echo "foreign replacement content" > "$target/replacement_sentinel.txt"\n'
        '    exit 42\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py_a.chmod(0o755)

    env_a = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py_a))
    res_a = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv_a)],
        capture_output=True,
        text=True,
        check=False,
        env=env_a,
    )
    assert res_a.returncode == 42
    assert "identity changed" in res_a.stderr
    assert target_venv_a.exists(), "Replaced directory must not be deleted by cleanup"
    sentinel_a = target_venv_a / "replacement_sentinel.txt"
    assert sentinel_a.exists()
    assert sentinel_a.read_text(encoding="utf-8").strip() == "foreign replacement content"

    # Case B: Claimed directory replaced by a symlink to an external directory
    target_venv_b = tmp_path / "venv_g1_replaced_symlink"
    external_dir = tmp_path / "external_protected_dir"
    external_dir.mkdir()
    (external_dir / "protected.txt").write_text("precious data\n", encoding="utf-8")

    fake_py_b = tmp_path / "fake_py_replace_symlink.sh"
    fake_py_b.write_text(
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
        '    rmdir "$target"\n'
        f'    ln -s "{external_dir}" "$target"\n'
        '    exit 43\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_py_b.chmod(0o755)

    env_b = dict(os.environ, AGENT_REVIEW_PYTHON=str(fake_py_b))
    res_b = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv_b)],
        capture_output=True,
        text=True,
        check=False,
        env=env_b,
    )
    assert res_b.returncode == 43
    assert "is a symlink" in res_b.stderr
    assert (external_dir / "protected.txt").exists(), "Symlink target must not be deleted"
    assert (external_dir / "protected.txt").read_text(encoding="utf-8") == "precious data\n"


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

    # Case 3: SIGTERM + cleanup rm failure (exit code 73) -> must still exit 143
    fake_bin = tmp_path / "bin_fail_rm"
    fake_bin.mkdir()
    fake_rm = fake_bin / "rm"
    fake_rm.write_text(
        "#!/bin/sh\n"
        'echo "simulated rm failure witness" >&2\n'
        "exit 73\n",
        encoding="utf-8",
    )
    fake_rm.chmod(0o755)

    target_venv_term_fail = tmp_path / "venv_g3_sigterm_rm_fail"
    env_term_fail = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py_term),
        PATH=f"{fake_bin}:{os.environ.get('PATH', '')}",
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


def test_countermodel_h1_a_replacement_after_fd_anchor_rejected(tmp_path: Path) -> None:
    """H1-A: When a directory replacement occurs after fd anchor but before identity acquisition
    finalizes, the replacement is rejected and foreign directory/sentinel state is preserved.
    """
    target_venv = tmp_path / "venv_h1a_target"
    sentinel_text = "FOREIGN_SENTINEL_H1A_DATA\n"
    host_py = sys.executable

    fake_py = tmp_path / "fake_py_h1a.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        script = sys.argv[4]
        private_dir = sys.argv[5]
        target = sys.argv[6]
        token_file = sys.argv[7]
        orig_stat = os.stat
        def hooked_stat(path, *args, **kwargs):
            if str(path) == target:
                os.rename(target, target + "_stolen")
                os.mkdir(target)
                p = os.path.join(target, "foreign_sentinel.txt")
                with open(p, "w") as f:
                    f.write({repr(sentinel_text)})
            return orig_stat(path, *args, **kwargs)
        os.stat = hooked_stat
        sys.argv = ["<claim>", private_dir, target, token_file]
        exec(script)
        sys.exit(0)
if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    sys.exit(42)
sys.exit(1)
""",
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
    assert "was substituted before acquisition finalized" in res.stderr
    assert target_venv.exists(), "Replacement directory must not be deleted"
    sentinel_file = target_venv / "foreign_sentinel.txt"
    assert sentinel_file.exists(), "Foreign sentinel must remain intact"
    assert sentinel_file.read_text(encoding="utf-8") == sentinel_text
    assert Path(f"{target_venv}_stolen").exists(), "Original anchored directory was renamed away"


def test_countermodel_h1_b_identity_capture_failure_refuses_destructive_cleanup(tmp_path: Path) -> None:
    """H1-B: When directory identity acquisition fails, ARM_CLEANUP must never authorize rm -rf
    of an unverified pathname, and foreign state is preserved in real installer execution.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_h1b_target"

    fake_py = tmp_path / "fake_py_h1b.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        target = sys.argv[6]
        os.mkdir(target)
        with open(os.path.join(target, "protected.txt"), "w") as f:
            f.write("PROTECTED_UNVERIFIED_DATA\\n")
        sys.exit(4)
if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    sys.exit(42)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py),
    )
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 2
    assert "unable to acquire verified directory object identity" in res.stderr
    assert target_venv.exists(), "Target with unverified identity must NOT be deleted"
    protected_file = target_venv / "protected.txt"
    assert protected_file.exists(), "Protected file in foreign target must be preserved"
    assert protected_file.read_text(encoding="utf-8").strip() == "PROTECTED_UNVERIFIED_DATA"


def test_countermodel_h1_c_replacement_between_mkdir_and_open_rejected(tmp_path: Path) -> None:
    """H1-C: When a target directory is replaced before publication with a foreign object B
    containing FOREIGN_PREOPEN_SENTINEL, the installer rejects claim and refuses mutation/cleanup of B.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_h1c_target"

    fake_py = tmp_path / "fake_py_h1c.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        script = sys.argv[4]
        private_dir = sys.argv[5]
        target = sys.argv[6]
        token_file = sys.argv[7]
        os.mkdir(target)
        with open(os.path.join(target, "foreign_sentinel.txt"), "w") as f:
            f.write("FOREIGN_PREOPEN_SENTINEL\\n")
        sys.argv = ["<claim>", private_dir, target, token_file]
        exec(script)
        sys.exit(0)
if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    print("ERROR: venv must not be invoked on foreign target", file=sys.stderr)
    sys.exit(42)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py),
    )
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 2
    assert "AgentReview toolrepo venv target must be absent" in res.stderr
    assert target_venv.exists(), "Foreign target B must NOT be deleted by installer failure"
    sentinel_file = target_venv / "foreign_sentinel.txt"
    assert sentinel_file.exists(), "Foreign sentinel in object B must remain intact"
    assert sentinel_file.read_text(encoding="utf-8").strip() == "FOREIGN_PREOPEN_SENTINEL"


def test_countermodel_h1_d_identity_acquisition_failure_preserves_foreign_empty_target(tmp_path: Path) -> None:
    """H1-D: When identity acquisition fails on a foreign empty directory B, error paths must never
    invoke rmdir or recursive deletion on the target pathname.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_h1d_target"

    fake_py = tmp_path / "fake_py_h1d.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        target = sys.argv[6]
        os.mkdir(target, 0o000)
        sys.exit(4)
if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    sys.exit(42)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py),
    )
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    try:
        assert res.returncode == 2
        assert "unable to acquire verified directory object identity" in res.stderr
        assert target_venv.exists(), "Foreign empty directory B must NOT be deleted by claim failure error paths"
    finally:
        if target_venv.exists():
            target_venv.chmod(0o755)


def test_countermodel_h1_e_empty_replacement_between_create_and_open_not_claimed(tmp_path: Path) -> None:
    """H1-E: When a foreign empty directory B is substituted at target before publication,
    the no-replace publication fails (EEXIST), B is never claimed or mutated by python -m venv,
    B is never deleted, and the installer exits fail-closed.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_h1e_target"

    witness_file = tmp_path / "venv_invoked_witness.txt"
    fake_py = tmp_path / "fake_py_h1e.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        script = sys.argv[4]
        private_dir = sys.argv[5]
        target = sys.argv[6]
        token_file = sys.argv[7]
        # Adversary creates EMPTY directory B at target before publication:
        os.mkdir(target)
        sys.argv = ["<claim>", private_dir, target, token_file]
        exec(script)
        sys.exit(0)

if sys.argv[1:5] == ["-I", "-S", "-m", "venv"]:
    with open("{witness_file}", "w") as f:
        f.write("WITNESS_INVOKED")
    print("FATAL: venv invoked on foreign empty target", file=sys.stderr)
    sys.exit(42)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    env = dict(
        os.environ,
        AGENT_REVIEW_PYTHON=str(fake_py),
    )
    res = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), str(target_venv)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 2
    assert "AgentReview toolrepo venv target must be absent" in res.stderr
    assert not witness_file.exists(), "python -m venv must NEVER be invoked on foreign empty directory B"
    assert target_venv.exists(), "Foreign empty directory B must NOT be deleted by installer"
    remaining = list(tmp_path.glob(".agent_review_claim.*"))
    assert len(remaining) == 0, f"Residual private claim dirs must be cleaned up: {remaining}"


def test_claim_phase_sigterm_exits_143_and_cleans_partial_target(tmp_path: Path) -> None:
    """Claim-phase signal probe (SIGTERM): When SIGTERM is received during the claim
    phase, the installer exits 143 and removes owned claim state.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_claim_sigterm"

    fake_py = tmp_path / "fake_py_claim_sigterm.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os, signal

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

sys.exit(1)
""",
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
    assert res.returncode == 143, f"SIGTERM must exit with code 143, got {res.returncode}"
    assert not target_venv.exists(), "Partial target must be cleaned up on SIGTERM, leaving no residual"
    remaining = list(tmp_path.glob(".agent_review_claim.*"))
    assert len(remaining) == 0, f"Owned claim state must be cleaned up on SIGTERM: {remaining}"


def test_claim_phase_sigint_exits_130_and_cleans_partial_target(tmp_path: Path) -> None:
    """Claim-phase signal probe (SIGINT): When SIGINT is received during the claim
    phase, the installer exits 130 and removes owned claim state.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_claim_sigint"

    fake_py = tmp_path / "fake_py_claim_sigint.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os, signal

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGINT)

sys.exit(1)
""",
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
    assert res.returncode == 130, f"SIGINT must exit with code 130, got {res.returncode}"
    assert not target_venv.exists(), "Partial target must be cleaned up on SIGINT, leaving no residual"
    remaining = list(tmp_path.glob(".agent_review_claim.*"))
    assert len(remaining) == 0, f"Owned claim state must be cleaned up on SIGINT: {remaining}"


def test_claim_phase_real_parent_sigterm_exits_143_and_cleans_claim_state(tmp_path: Path) -> None:
    """Real parent claim-phase signal test: launch real installer, claim helper reaches
    deterministic barrier after owned claim state exists and remains blocked, test process
    sends os.kill(installer_bash_pid, SIGTERM). Verifies installer exits 143 immediately via
    wait trap interruption, cleans owned private claim directory, never deletes foreign paths,
    and subsequent retry is not blocked by owned residual.
    """
    host_py = sys.executable
    target_venv = tmp_path / "venv_parent_sigterm"
    foreign_path = tmp_path / "foreign_data.txt"
    foreign_path.write_text("FOREIGN_DATA_KEEP_INTACT\n", encoding="utf-8")
    barrier = tmp_path / "claim_barrier.txt"

    fake_py = tmp_path / "fake_py_parent_sigterm.py"
    fake_py.write_text(
        f"""#!{host_py}
import sys, os, time

if sys.argv[1:4] == ["-I", "-S", "-c"]:
    if len(sys.argv) == 5:
        print("OK")
        sys.exit(0)
    if len(sys.argv) == 6:
        script = sys.argv[4]
        target_raw = sys.argv[5]
        sys.argv = ["<norm>", target_raw]
        exec(script)
        sys.exit(0)
    if len(sys.argv) == 8:
        # Claim helper: private claim dir exists and is owned
        with open("{barrier}", "w") as f:
            f.write("BARRIER_REACHED")
        while True:
            time.sleep(0.05)

sys.exit(1)
""",
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
        if barrier.exists():
            break
        time.sleep(0.1)
    assert barrier.exists(), "Claim helper did not reach deterministic barrier"

    # Send SIGTERM to the parent installer bash process directly
    os.kill(p.pid, signal.SIGTERM)

    try:
        stdout, stderr = p.communicate(timeout=3)
        assert p.returncode == 143, f"Installer must exit 143 on SIGTERM, got {p.returncode}; stderr: {stderr}"
        assert not target_venv.exists(), "Target venv must not exist after signal"
        assert foreign_path.exists(), "Foreign path must never be deleted"
        assert foreign_path.read_text(encoding="utf-8").strip() == "FOREIGN_DATA_KEEP_INTACT"
        remaining = list(tmp_path.glob(".agent_review_claim.*"))
        assert len(remaining) == 0, f"Owned temporary claim directory was not cleaned: {remaining}"
    except subprocess.TimeoutExpired:
        p.kill()
        assert False, "Installer hung and did not handle parent SIGTERM within timeout"
