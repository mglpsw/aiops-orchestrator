from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_review.target_pack_build_v2 import (
    BUILD_TEMPLATE_ROOT_MISSING_REASON_V2,
    TargetPackBuildError,
    build_target_pack_manifest_v2,
    load_seed_content_by_path_v2,
)
from app.agent_review.target_pack_manifest_v2 import TargetPackFileOwnershipV2

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_from_the_real_toolrepo_template_tree_succeeds() -> None:
    manifest = build_target_pack_manifest_v2(
        toolrepo_root=REPO_ROOT, toolrepo_sha="1" * 40, pack_version="0.1.0"
    )
    paths = {entry.path for entry in manifest.generated_files}
    assert ".aiops/target-profile.v2.yaml" in paths
    assert len(manifest.schema_digests) > 0


def test_build_target_relative_path_differs_from_template_source_path() -> None:
    """The exact bug caught during implementation smoke-testing: the
    template SOURCE lives at `templates/agentreview-v2-target-pack/target-
    profile.v2.yaml`, but the TARGET install path is `.aiops/target-
    profile.v2.yaml` -- these must never be conflated."""

    manifest = build_target_pack_manifest_v2(
        toolrepo_root=REPO_ROOT, toolrepo_sha="1" * 40, pack_version="0.1.0"
    )
    profile_entry = next(e for e in manifest.generated_files if "target-profile" in e.path)
    assert profile_entry.path == ".aiops/target-profile.v2.yaml"
    assert profile_entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED

    template_source = REPO_ROOT / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml"
    assert template_source.is_file()


def test_build_refuses_when_the_template_root_is_missing(tmp_path: Path) -> None:
    with pytest.raises(TargetPackBuildError) as exc_info:
        build_target_pack_manifest_v2(toolrepo_root=tmp_path, toolrepo_sha="1" * 40, pack_version="0.1.0")
    assert exc_info.value.reason_code == BUILD_TEMPLATE_ROOT_MISSING_REASON_V2


def test_seed_content_matches_the_manifests_own_digests() -> None:
    import hashlib

    manifest = build_target_pack_manifest_v2(
        toolrepo_root=REPO_ROOT, toolrepo_sha="1" * 40, pack_version="0.1.0"
    )
    seed_content = load_seed_content_by_path_v2(toolrepo_root=REPO_ROOT)
    for entry in manifest.generated_files:
        assert hashlib.sha256(seed_content[entry.path]).hexdigest() == entry.content_sha256


def test_the_shipped_profile_template_actually_validates_as_a_target_profile(tmp_path: Path) -> None:
    """Not just present -- the seed content this pack ships must be a
    STRUCTURALLY VALID `TargetProfileV2`, so a freshly-`init`'d target can
    be immediately validated/doctored (missing only the target's own
    `required_checks`/`repo` edits, never a schema-shape error)."""

    from app.agent_review.profile_loader_v2 import load_target_profile_v2

    seed_content = load_seed_content_by_path_v2(toolrepo_root=REPO_ROOT)
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_bytes(
        seed_content[".aiops/target-profile.v2.yaml"]
    )
    profile = load_target_profile_v2(tmp_path)
    assert profile.identity.repo == "OWNER/REPO"
