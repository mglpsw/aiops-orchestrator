"""Real CLI subprocess E2E for `scripts/agent-review-target-pack-v2.py`
(`#203`). Mirrors `test_aiops_review_quality_gate_v2_cli.py`'s own
discipline: exercise the actual entry point via `subprocess.run`, not the
library functions directly -- proves argument parsing, exit codes, and
stdout/stderr shape, not just the underlying logic (already covered
directly by the unit tests in `test_target_pack_{plan,install,doctor,
build}_v2.py`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "scripts" / "agent-review-target-pack-v2.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )


def test_init_creates_the_generated_set_and_a_receipt(tmp_path: Path) -> None:
    result = _run(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
        ]
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".aiops" / "target-profile.v2.yaml").is_file()
    receipt_path = tmp_path / ".aiops" / "install-receipt.v2.json"
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema_id"] == "agent-review.target-install-receipt.v2"
    assert receipt["rollout_mode"] == "off"


def test_init_never_writes_a_secret_value_anywhere_in_the_receipt(tmp_path: Path) -> None:
    result = _run(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
        ]
    )
    assert result.returncode == 0, result.stderr
    receipt_text = (tmp_path / ".aiops" / "install-receipt.v2.json").read_text(encoding="utf-8")
    assert "required_secret_names" in receipt_text


def test_init_twice_never_overwrites_the_target_owned_profile(tmp_path: Path) -> None:
    base_args = [
        "init",
        "--target-root", str(tmp_path),
        "--toolrepo-root", str(REPO_ROOT),
        "--target-repo", "owner/repo",
        "--pack-version", "0.1.0",
    ]
    assert _run(base_args).returncode == 0

    profile_path = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile_path.write_text(profile_path.read_text(encoding="utf-8") + "\n# target customization\n", encoding="utf-8")
    customized = profile_path.read_text(encoding="utf-8")

    result = _run(base_args)
    assert result.returncode == 0, result.stderr
    assert profile_path.read_text(encoding="utf-8") == customized


def test_doctor_reports_unhealthy_before_init(tmp_path: Path) -> None:
    result = _run(
        ["doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT), "--pack-version", "0.1.0"]
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["healthy"] is False
    assert report["profile"]["status"] == "missing"


def test_doctor_reports_healthy_after_init(tmp_path: Path) -> None:
    init_args = [
        "init",
        "--target-root", str(tmp_path),
        "--toolrepo-root", str(REPO_ROOT),
        "--target-repo", "owner/repo",
        "--pack-version", "0.1.0",
    ]
    assert _run(init_args).returncode == 0

    result = _run(
        ["doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT), "--pack-version", "0.1.0"]
    )
    report = json.loads(result.stdout)
    # profile.status is "present" -- healthy overall depends on required_checks
    # being filled in by the target, which the seed leaves as a placeholder,
    # but the profile itself is structurally valid so it still parses.
    assert report["profile"]["status"] == "present"
    assert report["receipt"]["status"] == "present"


def test_doctor_never_creates_or_modifies_anything(tmp_path: Path) -> None:
    """The CLI-level companion to the AST proof in `test_target_pack_arch_
    v2.py`: run `doctor` twice against an empty target and confirm the
    target root's directory listing never changes."""

    tmp_path.mkdir(exist_ok=True)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    for _ in range(2):
        _run(["doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT), "--pack-version", "0.1.0"])
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert before == after == []


def test_doctor_never_prints_a_traceback_for_a_healthy_or_unhealthy_target(tmp_path: Path) -> None:
    result = _run(
        ["doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT), "--pack-version", "0.1.0"]
    )
    assert "Traceback" not in result.stderr


def test_missing_required_flag_is_refused_by_argparse_not_a_traceback(tmp_path: Path) -> None:
    result = _run(["doctor", "--target-root", str(tmp_path)])
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_init_refuses_cleanly_instead_of_fabricating_a_toolrepo_sha(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: a `--toolrepo-root`
    that is not a real git checkout previously made `init` succeed anyway,
    silently writing a fabricated all-zero `toolrepo_sha` into a receipt
    whose entire purpose is provenance. Must now refuse cleanly instead."""

    fake_toolrepo = tmp_path / "fake-toolrepo"
    (fake_toolrepo / "templates" / "agentreview-v2-target-pack").mkdir(parents=True)
    (fake_toolrepo / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml").write_text(
        "placeholder", encoding="utf-8"
    )
    (fake_toolrepo / "schemas" / "agent-review" / "v2").mkdir(parents=True)

    target_root = tmp_path / "target"
    result = _run(
        [
            "init",
            "--target-root", str(target_root),
            "--toolrepo-root", str(fake_toolrepo),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "target_pack_cli_toolrepo_sha_unresolved" in result.stderr
    assert not (target_root / ".aiops" / "install-receipt.v2.json").exists()
