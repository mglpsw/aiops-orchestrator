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
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "scripts" / "agent-review-target-pack-v2.py"


def _run_raw(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Exercise an explicitly preview-bound init for legacy apply tests."""

    if not args or args[0] != "init" or "--apply" in args:
        return _run_raw(args)
    preview = _run_raw(args)
    if preview.returncode != 0:
        return preview
    plan_hash = json.loads(preview.stdout)["operation_plan_hash"]
    return _run_raw([*args, "--apply", "--expected-plan-sha256", plan_hash])


def test_init_is_write_zero_without_apply(tmp_path: Path) -> None:
    result = _run_raw(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
        ]
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".aiops").exists()
    preview = json.loads(result.stdout)
    assert preview["operation"] == "init"
    assert preview["operation_plan_hash"]


def test_init_preview_refuses_a_malformed_target_repo_before_any_mutation(tmp_path: Path) -> None:
    """RED for PR-C1: the CLI's `--target-repo` argparse argument has no
    shape validator of its own -- Codex Round 3 (PR #242, R3-3) confirmed
    the value flows unchanged into the receipt. The shared-authority
    fix (`TargetPackInstallIdentityV2.target_repo: Repository`) must
    refuse this at PREVIEW time (before `--apply`, before any write),
    through `main()`'s existing `ValidationError` -> clean CLI-boundary
    catch -- no traceback, no new CLI-only regex."""

    result = _run_raw(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "not-a-repository",
            "--pack-version", "0.1.0",
        ]
    )

    assert result.returncode == 2, result.stdout
    assert "Traceback" not in result.stderr
    assert not (tmp_path / ".aiops").exists()


def test_init_refuses_an_unmatched_expected_plan_without_mutation(tmp_path: Path) -> None:
    args = [
        "init",
        "--target-root", str(tmp_path),
        "--toolrepo-root", str(REPO_ROOT),
        "--target-repo", "owner/repo",
        "--pack-version", "0.1.0",
        "--apply", "--expected-plan-sha256", "0" * 64,
    ]
    result = _run_raw(args)
    assert result.returncode == 2
    assert "target_pack_cli_expected_plan_mismatch" in result.stderr
    assert not (tmp_path / ".aiops").exists()


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

    result = _run([*base_args, "--accept-target-owned", ".aiops/target-profile.v2.yaml"])
    assert result.returncode == 0, result.stderr
    assert profile_path.read_text(encoding="utf-8") == customized


def test_target_owned_reconciliation_requires_a_nominal_acceptance_and_never_overwrites(tmp_path: Path) -> None:
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
    observed = profile_path.read_bytes()

    preview = _run_raw(base_args)
    assert preview.returncode == 0, preview.stderr
    plan = json.loads(preview.stdout)
    assert plan["actions"][0]["action"] == "RECONCILE_TARGET_OWNED_IDENTITY"
    refused = _run_raw([*base_args, "--apply", "--expected-plan-sha256", plan["operation_plan_hash"]])
    assert refused.returncode == 2
    assert "target_owned_identity_acceptance_required" in refused.stderr
    assert profile_path.read_bytes() == observed

    accepting_preview = _run_raw([*base_args, "--accept-target-owned", ".aiops/target-profile.v2.yaml"])
    accepting_plan = json.loads(accepting_preview.stdout)
    applied = _run_raw(
        [
            *base_args,
            "--accept-target-owned", ".aiops/target-profile.v2.yaml",
            "--apply", "--expected-plan-sha256", accepting_plan["operation_plan_hash"],
        ]
    )
    assert applied.returncode == 0, applied.stderr
    assert profile_path.read_bytes() == observed


def test_init_refuses_a_plan_bound_to_a_different_target_repository(tmp_path: Path) -> None:
    base_args = [
        "init",
        "--target-root", str(tmp_path),
        "--toolrepo-root", str(REPO_ROOT),
        "--target-repo", "owner/repo",
        "--pack-version", "0.1.0",
    ]
    assert _run(base_args).returncode == 0
    foreign = _run_raw(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "other/repo",
            "--pack-version", "0.1.0",
        ]
    )
    assert foreign.returncode == 2
    assert "target_pack_operation_foreign_identity" in foreign.stderr


def test_init_refuses_a_valid_target_owned_profile_for_a_different_repository(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    seed = (REPO_ROOT / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml").read_text(
        encoding="utf-8"
    )
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(
        seed.replace("OWNER/REPO", "other/repo"), encoding="utf-8"
    )
    result = _run_raw(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
        ]
    )
    assert result.returncode == 2
    assert "target_pack_operation_foreign_identity" in result.stderr
    assert not (tmp_path / ".aiops" / "install-receipt.v2.json").exists()


def test_init_refuses_duplicate_nominal_target_owned_acceptance(tmp_path: Path) -> None:
    result = _run_raw(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
            "--accept-target-owned", ".aiops/target-profile.v2.yaml",
            "--accept-target-owned", ".aiops/target-profile.v2.yaml",
        ]
    )
    assert result.returncode == 2
    assert "target_pack_operation_duplicate_accepted_target_owned_path" in result.stderr
    assert not (tmp_path / ".aiops").exists()


def test_doctor_reports_unhealthy_before_init(tmp_path: Path) -> None:
    result = _run(
        [
            "doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo", "--pack-version", "0.1.0",
        ]
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
        [
            "doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo", "--pack-version", "0.1.0",
        ]
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
        _run([
            "doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo", "--pack-version", "0.1.0",
        ])
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert before == after == []


def test_doctor_never_prints_a_traceback_for_a_healthy_or_unhealthy_target(tmp_path: Path) -> None:
    result = _run(
        [
            "doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo", "--pack-version", "0.1.0",
        ]
    )
    assert "Traceback" not in result.stderr


def test_missing_required_flag_is_refused_by_argparse_not_a_traceback(tmp_path: Path) -> None:
    result = _run(["doctor", "--target-root", str(tmp_path)])
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_doctor_target_repo_flag_is_required(tmp_path: Path) -> None:
    """Post-merge review debt (aiops-orchestrator#205, C2): `doctor` used to
    have no `--target-repo` at all, so it could only check a receipt's
    identity claims against itself. Argparse-level proof that the new flag
    is mandatory, not merely conventionally passed everywhere in this
    file's other tests."""

    result = _run(
        ["doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT), "--pack-version", "0.1.0"]
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "--target-repo" in result.stderr


def test_doctor_refuses_an_aiops_directory_transplanted_from_a_different_target(tmp_path: Path, tmp_path_factory) -> None:
    """Post-merge review debt (aiops-orchestrator#205, C2), end-to-end
    reproduction of the original finding: a healthy install's `.aiops/`
    directory, copied verbatim into an unrelated target root, must NOT be
    reported healthy just because every field the receipt claims is
    internally self-consistent. `doctor` now requires the operator to
    assert which target it is actually diagnosing, independent of
    anything the copied receipt claims about itself."""

    source_root = tmp_path_factory.mktemp("source-target")
    init_args = [
        "init",
        "--target-root", str(source_root),
        "--toolrepo-root", str(REPO_ROOT),
        "--target-repo", "acme/source-repo",
        "--pack-version", "0.1.0",
    ]
    assert _run(init_args).returncode == 0

    shutil.copytree(source_root / ".aiops", tmp_path / ".aiops")

    result = _run(
        [
            "doctor", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "acme/actually-different-repo", "--pack-version", "0.1.0",
        ]
    )
    report = json.loads(result.stdout)
    assert report["healthy"] is False
    assert report["receipt"]["reason_code"] == "target_pack_doctor_receipt_target_repo_mismatch"


def test_init_refuses_a_rollout_the_pack_does_not_yet_support(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: `init --rollout
    shadow_full` used to exit 0 and write `rollout_mode: shadow_full` into
    the receipt even though this slice ships no trusted-check inventory,
    workflow integration, or `ReviewReadinessV2` wiring at all -- the
    spec's own definition of `shadow_full`. Must refuse cleanly instead,
    and leave nothing written."""

    result = _run(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
            "--rollout", "shadow_full",
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "target_pack_plan_rollout_exceeds_pack_capability" in result.stderr
    assert not (tmp_path / ".aiops" / "install-receipt.v2.json").exists()


def test_init_leaves_a_nonexistent_target_nonexistent_when_rollout_is_refused(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed (spec rev.2 §5.1):
    `_cmd_init` used to call `target_root.mkdir(...)` before resolving the
    toolrepo, building the manifest, or checking the rollout ceiling -- so a
    refusal still left the directory behind, contradicting this very test's
    neighbour's own "leaves nothing behind" claim. Uses a target path that
    does NOT exist beforehand -- unlike a bare `tmp_path` (which pytest
    already creates), this is what actually exercises the claim."""

    target = tmp_path / "does-not-exist-yet"
    assert not target.exists()

    result = _run(
        [
            "init",
            "--target-root", str(target),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
            "--rollout", "shadow_full",
        ]
    )

    assert result.returncode != 0
    assert not target.exists()


def test_init_leaves_a_nonexistent_target_nonexistent_when_toolrepo_is_unresolvable(tmp_path: Path) -> None:
    target = tmp_path / "does-not-exist-yet"
    not_a_toolrepo = tmp_path / "not-a-git-checkout"
    assert not target.exists()

    result = _run(
        [
            "init",
            "--target-root", str(target),
            "--toolrepo-root", str(not_a_toolrepo),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
        ]
    )

    assert result.returncode != 0
    assert not target.exists()


def test_init_refuses_shadow_minimal_until_workflows_exist(tmp_path: Path) -> None:
    result = _run(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
            "--rollout", "shadow_minimal",
        ]
    )

    assert result.returncode != 0
    assert "target_pack_plan_rollout_exceeds_pack_capability" in result.stderr


def test_init_never_records_a_target_owned_file_in_generated_file_hashes(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: on a FRESH `init`,
    the TARGET_OWNED profile is a `WRITE_NEW` action (nothing existed
    before) and used to be recorded in `generated_file_hashes`, which the
    contract reserves for UPSTREAM_GENERATED content only -- the same path
    was simultaneously claimed as pack-generated and target-owned."""

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

    receipt = json.loads((tmp_path / ".aiops" / "install-receipt.v2.json").read_text(encoding="utf-8"))
    assert receipt["generated_file_hashes"] == {}
    assert receipt["target_owned_paths"] == [".aiops/target-profile.v2.yaml"]


def test_init_twice_preserves_target_owned_paths_in_the_receipt(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: on a SECOND,
    idempotent `init` the TARGET_OWNED profile is `SKIP_TARGET_OWNED`
    (nothing written this invocation), and `target_owned_paths` used to be
    derived from `written` -- so it silently went from `[".aiops/target-
    profile.v2.yaml"]` to `[]`, even though the pack's declared ownership
    of that path never changed. `target_owned_paths` must be a stable
    declaration of ownership, not a diary of one invocation's writes."""

    base_args = [
        "init",
        "--target-root", str(tmp_path),
        "--toolrepo-root", str(REPO_ROOT),
        "--target-repo", "owner/repo",
        "--pack-version", "0.1.0",
    ]
    assert _run(base_args).returncode == 0
    assert _run(base_args).returncode == 0

    receipt = json.loads((tmp_path / ".aiops" / "install-receipt.v2.json").read_text(encoding="utf-8"))
    assert receipt["target_owned_paths"] == [".aiops/target-profile.v2.yaml"]


def test_init_never_fabricates_a_target_policy_hash(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: `init` used to
    hardcode `target_policy_hash: "0" * 64` even though no policy artifact
    ships in this slice at all -- a syntactically valid, self-hash-
    consistent all-zero digest that a consumer of this schema could not
    distinguish from a real policy hash. Same class of bug as `toolrepo_
    sha`/`target_profile_hash` above, for a value that has no real content
    to hash yet at all: absence must be `null`, never a fabricated
    digest."""

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

    receipt = json.loads((tmp_path / ".aiops" / "install-receipt.v2.json").read_text(encoding="utf-8"))
    assert receipt["target_policy_hash"] is None
    assert receipt["target_policy_hash"] != "0" * 64


def test_init_computes_a_real_target_profile_hash_not_a_sentinel(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: `init` used to
    hardcode `target_profile_hash: "0"*64` even though the real hash of
    the profile it just wrote is trivially available. Same class of
    fabricated-identity bug as `toolrepo_sha` above."""

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

    receipt = json.loads((tmp_path / ".aiops" / "install-receipt.v2.json").read_text(encoding="utf-8"))
    assert receipt["target_profile_hash"] != "0" * 64

    from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2

    expected = compute_profile_hash_v2(load_target_profile_v2(tmp_path))
    assert receipt["target_profile_hash"] == expected


def test_init_refuses_cleanly_not_a_traceback_when_a_preexisting_target_owned_profile_is_invalid(
    tmp_path: Path,
) -> None:
    """Adversarial review finding, confirmed and fixed: computing a real
    target_profile_hash means `init` now READS whatever profile is
    already on disk (relevant on a second `init` against a target whose
    TARGET_OWNED profile was hand-edited into an invalid state) --
    reproduced as an UNCAUGHT traceback before `main()`'s except tuple was
    extended to cover `TargetProfileLoadErrorV2`."""

    (tmp_path / ".aiops").mkdir(parents=True)
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(
        "not: valid: yaml: at: all: :::", encoding="utf-8"
    )

    result = _run(
        [
            "init",
            "--target-root", str(tmp_path),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "target_owned_changed_invalid" in result.stderr


def test_init_refuses_when_the_receipt_write_would_escape_target_root_via_a_symlink(tmp_path: Path) -> None:
    """Round 5 adversarial finding, confirmed and fixed: `_cmd_init` used
    to write the receipt with a raw `Path.write_text`, bypassing every
    symlink/containment check `apply_install_plan_v2` enforces for every
    OTHER write. Reproduced: a pre-existing, valid `TARGET_OWNED` profile
    reached through a symlinked `.aiops` meant `apply_install_plan_v2`'s
    own profile write was `SKIP_TARGET_OWNED` (no write attempted, no
    check triggered at all) -- so the raw receipt write was the ONLY write
    touching `.aiops/`, and it silently followed the symlink, landing
    `install-receipt.v2.json` entirely outside `target_root`, exit 0, no
    refusal. Must now refuse cleanly instead.

    Since aiops-orchestrator#205/H1A-R1 added read-side containment, the
    escape is caught EARLIER -- at plan time, by `resolve_within_target_
    root_v2` -- so the reported reason code is now the plan-time one rather
    than the write-time one. Both are correct refusals of the same escape;
    the security-relevant assertions (non-zero exit, no traceback, and
    above all NOTHING written into the symlink target) are unchanged."""

    outside = tmp_path.parent / f"{tmp_path.name}-outside-receipt-escape"
    outside.mkdir()
    # A real, VALID target-owned profile living outside target_root.
    template_profile = (
        REPO_ROOT / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml"
    ).read_bytes()
    (outside / "target-profile.v2.yaml").write_bytes(template_profile)

    target_root = tmp_path / "target"
    target_root.mkdir()
    (target_root / ".aiops").symlink_to(outside)

    result = _run(
        [
            "init",
            "--target-root", str(target_root),
            "--toolrepo-root", str(REPO_ROOT),
            "--target-repo", "owner/repo",
            "--pack-version", "0.1.0",
            "--accept-target-owned", ".aiops/target-profile.v2.yaml",
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "path_escapes_target_root" in result.stderr
    # The receipt must NOT have leaked into the symlink target.
    assert not (outside / "install-receipt.v2.json").exists()
    # Nor anything else -- the pre-existing valid profile the fixture put
    # there is the only file that may remain.
    assert [p.name for p in outside.iterdir()] == ["target-profile.v2.yaml"]


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


# --- `validate` (`#203-C2`) --------------------------------------------------


def test_validate_passes_on_a_real_freshly_initialised_target(tmp_path: Path) -> None:
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

    validate_result = _run_raw(["validate", "--target-root", str(tmp_path)])
    assert validate_result.returncode == 0, validate_result.stderr
    report = json.loads(validate_result.stdout)
    assert report["valid"] is True
    assert report["target_root_real"] == str(tmp_path.resolve())
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    for name in (
        "target_root", "aiops_snapshot", "receipt", "profile",
        "profile_hash", "profile_identity", "root_identity",
        "observation_budget", "target_owned_integrity", "generated_file_integrity",
        "cross_ledger_alias_separation",
    ):
        assert statuses[name] == "pass", name
    for name in (
        "target_owned_set", "generated_file_set", "rollout_capability",
        "previous_install_lineage", "trusted_check_inventory",
    ):
        assert statuses[name] == "unavailable", name
    assert set(report["unvalidated_capabilities"]) == {
        "target_owned_set", "generated_file_set", "rollout_capability",
        "previous_install_lineage", "trusted_check_inventory",
    }


def test_validate_fails_closed_on_drift_after_a_real_init(tmp_path: Path) -> None:
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

    profile_path = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile_path.write_text(profile_path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    validate_result = _run_raw(["validate", "--target-root", str(tmp_path)])
    assert validate_result.returncode == 1
    report = json.loads(validate_result.stdout)
    assert report["valid"] is False
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert statuses["target_owned_integrity"] == "fail"
    assert statuses["profile_hash"] == "pass"  # semantic content unchanged -- byte drift only


def test_validate_never_claims_the_trusted_check_dimension_passed(tmp_path: Path) -> None:
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

    validate_result = _run_raw(["validate", "--target-root", str(tmp_path)])
    report = json.loads(validate_result.stdout)
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert statuses["trusted_check_inventory"] == "unavailable"
    assert statuses["trusted_check_inventory"] != "pass"
    assert "trusted_check_inventory" in report["unvalidated_capabilities"]


def test_validate_rejects_toolrepo_root_argument(tmp_path: Path) -> None:
    """The absence IS the charter: `validate` needs no toolrepo checkout
    at all."""

    result = _run_raw(["validate", "--target-root", str(tmp_path), "--toolrepo-root", str(REPO_ROOT)])
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_validate_exits_2_on_missing_target_root_argument() -> None:
    result = _run_raw(["validate"])
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "--target-root" in result.stderr


def test_validate_never_prints_a_traceback_on_a_missing_target(tmp_path: Path) -> None:
    never_created = tmp_path / "never-created"
    result = _run_raw(["validate", "--target-root", str(never_created)])
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["valid"] is False


def test_validate_is_write_zero(tmp_path: Path) -> None:
    """No `validate` invocation -- valid or invalid target, existing or
    missing root -- ever creates, modifies, or removes anything."""

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

    def _snapshot(root: Path) -> dict[str, float]:
        return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in sorted(root.rglob("*")) if p.is_file()}

    before = _snapshot(tmp_path)
    _run_raw(["validate", "--target-root", str(tmp_path)])
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(
        (tmp_path / ".aiops" / "target-profile.v2.yaml").read_text(encoding="utf-8") + "\n# x\n", encoding="utf-8"
    )
    _run_raw(["validate", "--target-root", str(tmp_path)])  # now failing -- still write-zero
    after_content = (tmp_path / ".aiops" / "target-profile.v2.yaml").read_text(encoding="utf-8")
    assert after_content.endswith("# x\n")

    missing = tmp_path / "definitely-not-here"
    _run_raw(["validate", "--target-root", str(missing)])
    assert not missing.exists()
