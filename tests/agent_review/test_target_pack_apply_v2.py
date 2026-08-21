"""REDs for the canonical authorized apply boundary under K EX."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import app.agent_review.target_pack_apply_v2 as apply_module
from app.agent_review.target_pack_apply_v2 import (
    TARGET_PACK_PLAN_STALE_REASON_V2,
    TargetPackAuthorizedApplyErrorV2,
    apply_authorized_target_pack_init_v2,
)
from app.agent_review.target_pack_build_v2 import build_target_pack_manifest_v2, load_seed_content_by_path_v2
from app.agent_review.target_pack_epoch_v2 import TARGET_PACK_EPOCH_BUSY_REASON_V2, TargetPackEpochError
from app.agent_review.target_pack_operation_v2 import compute_target_pack_operation_plan_v2

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pack_material() -> tuple[object, dict[str, bytes]]:
    sha = subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True).strip()
    manifest = build_target_pack_manifest_v2(toolrepo_root=REPO_ROOT, toolrepo_sha=sha, pack_version="0.1.0")
    return manifest, load_seed_content_by_path_v2(toolrepo_root=REPO_ROOT, toolrepo_sha=sha)


def _preview(*, manifest, seed: dict[str, bytes], target: Path, target_repo: str = "owner/repo"):
    return compute_target_pack_operation_plan_v2(
        manifest=manifest,
        target_root=target,
        target_repo=target_repo,
        rollout="off",
        seed_content_by_path=seed,
        previous_receipt=None,
    )


def _probe_shared_epoch(target: Path) -> str:
    code = """
from pathlib import Path
from app.agent_review.target_pack_epoch_v2 import TargetPackEpochError, acquire_target_pack_epoch_v2
try:
    lease = acquire_target_pack_epoch_v2(target_root=Path(__import__('sys').argv[1]), exclusive=False)
except TargetPackEpochError as exc:
    print(exc.reason_code)
else:
    print('acquired')
    lease.release()
"""
    return subprocess.check_output([sys.executable, "-c", code, str(target)], text=True).strip()


def test_plan_stale_leaves_arbitrarily_missing_target_prefixes_absent(pack_material, tmp_path: Path) -> None:
    """R24/M_MKDIR_BEFORE_AUTH/M_LATE_PLAN_GATE: zero target mutation first."""

    manifest, seed = pack_material
    target = tmp_path / "missing" / "many" / "prefixes" / "target"
    assert not target.exists()
    with pytest.raises(TargetPackAuthorizedApplyErrorV2) as exc_info:
        apply_authorized_target_pack_init_v2(
            manifest=manifest,
            target_root=target,
            target_repo="owner/repo",
            rollout="off",
            seed_content_by_path=seed,
            expected_plan_sha256="0" * 64,
            accepted_target_owned_paths=(),
        )
    assert exc_info.value.reason_code == TARGET_PACK_PLAN_STALE_REASON_V2
    assert not (tmp_path / "missing").exists()


def test_equal_locked_plan_materializes_missing_ancestors_and_applies_that_same_identity(pack_material, tmp_path: Path) -> None:
    """R25/R26: equality precedes materialization and there is no third plan."""

    manifest, seed = pack_material
    target = tmp_path / "a" / "b" / "c" / "target"
    preview = _preview(manifest=manifest, seed=seed, target=target)
    result = apply_authorized_target_pack_init_v2(
        manifest=manifest,
        target_root=target,
        target_repo="owner/repo",
        rollout="off",
        seed_content_by_path=seed,
        expected_plan_sha256=preview.plan.operation_plan_hash,
        accepted_target_owned_paths=(),
    )
    assert result.operation_plan_hash == preview.plan.operation_plan_hash
    assert (target / ".aiops" / "target-profile.v2.yaml").is_file()
    assert (target / ".aiops" / "install-receipt.v2.json").is_file()


def test_authorized_preview_is_not_reused_after_target_state_changes_before_k_ex(pack_material, tmp_path: Path) -> None:
    """M_PRELOCK_PLAN/M_APPLY_WITHOUT_EQUALITY: apply cannot substitute P'."""

    manifest, seed = pack_material
    target = tmp_path / "target"
    preview = _preview(manifest=manifest, seed=seed, target=target)
    # Simulate a prior mutation between preview and this apply invocation.
    profile = target / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_bytes(seed[".aiops/target-profile.v2.yaml"])
    with pytest.raises(TargetPackAuthorizedApplyErrorV2) as exc_info:
        apply_authorized_target_pack_init_v2(
            manifest=manifest,
            target_root=target,
            target_repo="owner/repo",
            rollout="off",
            seed_content_by_path=seed,
            expected_plan_sha256=preview.plan.operation_plan_hash,
            accepted_target_owned_paths=(),
        )
    assert exc_info.value.reason_code == TARGET_PACK_PLAN_STALE_REASON_V2
    assert not (target / ".aiops" / "install-receipt.v2.json").exists()


def test_receipt_is_written_under_the_same_live_k_epoch(pack_material, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R29/R31/R32: K EX spans locked planning, writes, and receipt."""

    manifest, seed = pack_material
    target = tmp_path / "target"
    preview = _preview(manifest=manifest, seed=seed, target=target)
    original_apply = apply_module.apply_install_plan_v2
    original_receipt = apply_module.write_receipt_v2
    observed: dict[str, object] = {}

    def record_apply(**kwargs):
        observed["lease"] = kwargs["lease"]
        return original_apply(**kwargs)

    def record_receipt(**kwargs):
        assert kwargs["lease"] is observed["lease"]
        assert _probe_shared_epoch(target) == TARGET_PACK_EPOCH_BUSY_REASON_V2
        return original_receipt(**kwargs)

    monkeypatch.setattr(apply_module, "apply_install_plan_v2", record_apply)
    monkeypatch.setattr(apply_module, "write_receipt_v2", record_receipt)
    result = apply_authorized_target_pack_init_v2(
        manifest=manifest,
        target_root=target,
        target_repo="owner/repo",
        rollout="off",
        seed_content_by_path=seed,
        expected_plan_sha256=preview.plan.operation_plan_hash,
        accepted_target_owned_paths=(),
    )
    assert result.written_paths[-1] == ".aiops/install-receipt.v2.json"
    assert _probe_shared_epoch(target) == "acquired"


def test_epoch_busy_refuses_before_missing_target_is_materialized(pack_material, tmp_path: Path) -> None:
    """R30: a reader can linearize before first install; writer does not race it."""

    from app.agent_review.target_pack_epoch_v2 import acquire_target_pack_epoch_v2

    manifest, seed = pack_material
    target = tmp_path / "missing" / "target"
    preview = _preview(manifest=manifest, seed=seed, target=target)
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=False):
        with pytest.raises(TargetPackEpochError) as exc_info:
            apply_authorized_target_pack_init_v2(
                manifest=manifest,
                target_root=target,
                target_repo="owner/repo",
                rollout="off",
                seed_content_by_path=seed,
                expected_plan_sha256=preview.plan.operation_plan_hash,
                accepted_target_owned_paths=(),
            )
    assert exc_info.value.reason_code == TARGET_PACK_EPOCH_BUSY_REASON_V2
    assert not (tmp_path / "missing").exists()
