from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)
from app.agent_review.target_pack_plan_v2 import (
    PLAN_PATH_RESOLUTION_FAILED_REASON_V2,
    PLAN_ROLLOUT_CEILING_EXCEEDED_REASON_V2,
    PlanError,
    PlannedActionV2,
    compute_install_plan_v2,
    resolve_within_target_root_v2,
    validate_rollout_ceiling_v2,
)
from app.agent_review.target_pack_receipt_v2 import TargetInstallReceiptV2, compute_target_install_receipt_hash_v2

import hashlib


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(*entries: GeneratedFileEntryV2) -> TargetPackManifestV2:
    return TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=entries,
        schema_digests={"x.json": "a" * 64},
        required_capabilities=(),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )


def _receipt(generated_file_hashes: dict[str, str]) -> TargetInstallReceiptV2:
    fields = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        manifest_digest="d" * 64,
        target_repo="owner/repo",
        portable_target_root_identity="e" * 64,
        target_profile_hash="a" * 64,
        target_policy_hash="b" * 64,
        review_pack_hashes={},
        generated_file_hashes=generated_file_hashes,
        target_owned_file_hashes={},
        target_owned_paths=(),
        required_capabilities=(),
        expected_runner_labels=(),
        required_secret_names=(),
        rollout_mode="off",
        compatibility="compatible",
        previous_install_identity=None,
        generated_at=None,
    )
    computed = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    )
    return TargetInstallReceiptV2(**fields, receipt_hash=computed)


def test_missing_file_plans_write_new(tmp_path: Path) -> None:
    manifest = _manifest(
        GeneratedFileEntryV2(
            path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256="c" * 64
        )
    )
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)
    assert plan.file_actions[0].action is PlannedActionV2.WRITE_NEW
    assert not plan.has_drift


def test_file_matching_seed_content_is_noop(tmp_path: Path) -> None:
    content = b"hello"
    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(content)
    )
    (tmp_path / "a.yaml").write_bytes(content)
    plan = compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=None)
    assert plan.file_actions[0].action is PlannedActionV2.NOOP_UNCHANGED
    assert plan.is_noop


def test_upstream_generated_file_unchanged_since_recorded_is_overwrite_safe(tmp_path: Path) -> None:
    old_content = b"old"
    new_content = b"new"
    entry = GeneratedFileEntryV2(
        path="a.yaml",
        ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED,
        content_sha256=_sha256(new_content),
    )
    (tmp_path / "a.yaml").write_bytes(old_content)
    receipt = _receipt({"a.yaml": _sha256(old_content)})
    plan = compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=receipt)
    assert plan.file_actions[0].action is PlannedActionV2.OVERWRITE_SAFE
    assert not plan.has_drift


def test_upstream_generated_file_diverging_from_receipt_is_drift(tmp_path: Path) -> None:
    """The target edited a file the pack believes it owns -- this is the
    core drift scenario the grant explicitly names."""

    entry = GeneratedFileEntryV2(
        path="a.yaml",
        ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED,
        content_sha256=_sha256(b"pack-seed-v2"),
    )
    (tmp_path / "a.yaml").write_bytes(b"target-hand-edited-this")
    receipt = _receipt({"a.yaml": _sha256(b"pack-seed-v1")})
    plan = compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=receipt)
    assert plan.file_actions[0].action is PlannedActionV2.REFUSE_DRIFT
    assert plan.has_drift
    assert plan.drifted_paths == ("a.yaml",)


def test_target_owned_file_present_is_skipped_never_diffed(tmp_path: Path) -> None:
    entry = GeneratedFileEntryV2(
        path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
        content_sha256=_sha256(b"seed"),
    )
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_bytes(b"target has heavily customized this")
    plan = compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=None)
    assert plan.file_actions[0].action is PlannedActionV2.SKIP_TARGET_OWNED
    assert not plan.has_drift


def test_target_owned_file_absent_is_write_new(tmp_path: Path) -> None:
    entry = GeneratedFileEntryV2(
        path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
        content_sha256=_sha256(b"seed"),
    )
    plan = compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=None)
    assert plan.file_actions[0].action is PlannedActionV2.WRITE_NEW


def test_merged_declarative_always_plans_a_fenced_merge(tmp_path: Path) -> None:
    entry = GeneratedFileEntryV2(
        path=".gitignore", ownership=TargetPackFileOwnershipV2.MERGED_DECLARATIVE, content_sha256="d" * 64
    )
    plan = compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=None)
    assert plan.file_actions[0].action is PlannedActionV2.MERGE_FENCED_BLOCK


@pytest.mark.parametrize(
    ("requested", "resolved"),
    [("off", "off"), ("shadow_minimal", "shadow_minimal"), ("shadow_full", "shadow_full"),
     ("shadow_full", "off"), ("shadow_minimal", "off")],
)
def test_rollout_ceiling_never_exceeded_is_accepted(requested: str, resolved: str) -> None:
    validate_rollout_ceiling_v2(requested=requested, resolved=resolved)


@pytest.mark.parametrize(
    ("requested", "resolved"),
    [("off", "shadow_minimal"), ("off", "shadow_full"), ("shadow_minimal", "shadow_full")],
)
def test_rollout_ceiling_exceeded_is_refused(requested: str, resolved: str) -> None:
    with pytest.raises(PlanError) as exc_info:
        validate_rollout_ceiling_v2(requested=requested, resolved=resolved)
    assert exc_info.value.reason_code == PLAN_ROLLOUT_CEILING_EXCEEDED_REASON_V2


# --- Round 5: Codex shadow review of #230 at fbc67db --------------------


def test_resolve_within_target_root_returns_a_typed_refusal_for_a_symlink_loop(tmp_path: Path) -> None:
    """RED. `Path.resolve(strict=False)` raises `RuntimeError` for a
    symlink loop, not `OSError` and not a return value -- unlike every
    other resolution outcome this function's callers already handle via a
    typed `PlanError`. Reproduced before the fix: an unhandled
    `RuntimeError` propagated out of `resolve_within_target_root_v2`."""
    (tmp_path / "a").symlink_to("b")
    (tmp_path / "b").symlink_to("a")

    with pytest.raises(PlanError) as exc_info:
        resolve_within_target_root_v2(tmp_path.resolve(strict=False), tmp_path / "a")
    assert exc_info.value.reason_code == PLAN_PATH_RESOLUTION_FAILED_REASON_V2


def test_compute_install_plan_refuses_a_symlink_loop_instead_of_crashing(tmp_path: Path) -> None:
    """RED, end to end. A symlink loop at a manifest-declared path used to
    crash `compute_install_plan_v2` with an unhandled `RuntimeError`
    instead of the typed `PlanError` refusal every other resolution
    failure in this module produces."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").symlink_to("target-profile.v2.yaml")
    entry = GeneratedFileEntryV2(
        path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
        content_sha256=_sha256(b"seed"),
    )

    with pytest.raises(PlanError) as exc_info:
        compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=None)
    assert exc_info.value.reason_code == PLAN_PATH_RESOLUTION_FAILED_REASON_V2


def test_read_on_disk_sha256_is_bound_to_the_captured_root_not_a_mutable_alias(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """RED. `_read_on_disk_sha256_v2` used to compose from `target_root /
    relative_path` -- the caller's mutable alias -- rather than
    `target_root_real`. Retargeting the alias to a divergent descendant
    AFTER `compute_install_plan_v2` captures its single resolution must
    have zero effect on what gets read."""
    root = tmp_path_factory.mktemp("root")
    (root / ".aiops").mkdir()
    (root / ".aiops" / "target-profile.v2.yaml").write_bytes(b"root-content")
    sub = root / "subdir"
    (sub / ".aiops").mkdir(parents=True)
    (sub / ".aiops" / "target-profile.v2.yaml").write_bytes(b"subdir-divergent-content")
    live = tmp_path_factory.mktemp("live-parent") / "live"
    live.symlink_to(root, target_is_directory=True)

    entry = GeneratedFileEntryV2(
        path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
        content_sha256=_sha256(b"seed"),
    )

    import app.agent_review.target_pack_plan_v2 as plan_module

    real_resolve = live.resolve(strict=False)
    live.unlink()
    live.symlink_to(sub, target_is_directory=True)
    try:
        on_disk_hash = plan_module._read_on_disk_sha256_v2(real_resolve, entry.path)
    finally:
        live.unlink()
        live.symlink_to(root, target_is_directory=True)

    assert on_disk_hash == _sha256(b"root-content")
    assert on_disk_hash != _sha256(b"subdir-divergent-content")
