from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.agent_review.target_pack_build_v2 import (
    BUILD_SCHEMA_TREE_UNREADABLE_REASON_V2,
    BUILD_TEMPLATE_ROOT_MISSING_REASON_V2,
    BUILD_TOOLREPO_SHA_INVALID_SHAPE_REASON_V2,
    TargetPackBuildError,
    build_target_pack_manifest_v2,
    load_seed_content_by_path_v2,
)
from app.agent_review.target_pack_manifest_v2 import TargetPackFileOwnershipV2

REPO_ROOT = Path(__file__).resolve().parents[2]


def _real_head_sha(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _init_git_repo(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, check=True)


def _commit_all(root: Path) -> str:
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=str(root), capture_output=True, text=True, check=True)
    return _real_head_sha(root)


def test_build_from_the_real_toolrepo_template_tree_succeeds() -> None:
    manifest = build_target_pack_manifest_v2(
        toolrepo_root=REPO_ROOT, toolrepo_sha=_real_head_sha(REPO_ROOT), pack_version="0.1.0"
    )
    paths = {entry.path for entry in manifest.generated_files}
    assert ".aiops/target-profile.v2.yaml" in paths
    assert len(manifest.schema_digests) > 0


def test_build_target_relative_path_differs_from_template_source_path() -> None:
    """The exact bug caught during implementation smoke-testing: the
    template SOURCE lives at `templates/agentreview-v2-target-pack/target-
    profile.v2.yaml`, but the TARGET install path is `.aiops/target-
    profile.v2.yaml` -- these must never be conflated."""

    sha = _real_head_sha(REPO_ROOT)
    manifest = build_target_pack_manifest_v2(toolrepo_root=REPO_ROOT, toolrepo_sha=sha, pack_version="0.1.0")
    profile_entry = next(e for e in manifest.generated_files if "target-profile" in e.path)
    assert profile_entry.path == ".aiops/target-profile.v2.yaml"
    assert profile_entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED

    committed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}:templates/agentreview-v2-target-pack/target-profile.v2.yaml"],
        capture_output=True,
    )
    assert committed.returncode == 0


def test_build_refuses_when_the_template_root_is_missing(tmp_path: Path) -> None:
    """A REAL git repo (so the refusal is specifically "no template tree at
    this SHA", not "not a git repo at all") that simply never had a
    `templates/agentreview-v2-target-pack/` directory committed."""

    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("nothing here\n", encoding="utf-8")
    sha = _commit_all(tmp_path)

    with pytest.raises(TargetPackBuildError) as exc_info:
        build_target_pack_manifest_v2(toolrepo_root=tmp_path, toolrepo_sha=sha, pack_version="0.1.0")
    assert exc_info.value.reason_code == BUILD_TEMPLATE_ROOT_MISSING_REASON_V2


def test_build_refuses_a_toolrepo_root_that_is_not_a_git_checkout(tmp_path: Path) -> None:
    with pytest.raises(TargetPackBuildError) as exc_info:
        build_target_pack_manifest_v2(toolrepo_root=tmp_path, toolrepo_sha="1" * 40, pack_version="0.1.0")
    assert exc_info.value.reason_code == BUILD_SCHEMA_TREE_UNREADABLE_REASON_V2


def test_build_refuses_a_malformed_toolrepo_sha() -> None:
    with pytest.raises(TargetPackBuildError) as exc_info:
        build_target_pack_manifest_v2(toolrepo_root=REPO_ROOT, toolrepo_sha="not-a-sha", pack_version="0.1.0")
    assert exc_info.value.reason_code == BUILD_TOOLREPO_SHA_INVALID_SHAPE_REASON_V2


def test_seed_content_matches_the_manifests_own_digests() -> None:
    import hashlib

    sha = _real_head_sha(REPO_ROOT)
    manifest = build_target_pack_manifest_v2(toolrepo_root=REPO_ROOT, toolrepo_sha=sha, pack_version="0.1.0")
    seed_content = load_seed_content_by_path_v2(toolrepo_root=REPO_ROOT, toolrepo_sha=sha)
    for entry in manifest.generated_files:
        assert hashlib.sha256(seed_content[entry.path]).hexdigest() == entry.content_sha256


def test_the_shipped_profile_template_actually_validates_as_a_target_profile(tmp_path: Path) -> None:
    """Not just present -- the seed content this pack ships must be a
    STRUCTURALLY VALID `TargetProfileV2`, so a freshly-`init`'d target can
    be immediately validated/doctored (missing only the target's own
    `required_checks`/`repo` edits, never a schema-shape error)."""

    from app.agent_review.profile_loader_v2 import load_target_profile_v2

    seed_content = load_seed_content_by_path_v2(toolrepo_root=REPO_ROOT, toolrepo_sha=_real_head_sha(REPO_ROOT))
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_bytes(
        seed_content[".aiops/target-profile.v2.yaml"]
    )
    profile = load_target_profile_v2(tmp_path)
    assert profile.identity.repo == "OWNER/REPO"


def test_pack_material_is_read_from_the_pinned_git_tree_not_the_dirty_working_tree(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: `build_target_pack_
    manifest_v2`/`load_seed_content_by_path_v2` used to read the WORKING
    TREE via `Path.read_bytes()` while `toolrepo_sha` was independently
    resolved via `git rev-parse HEAD` -- a dirty tracked template installed
    bytes the receipt's own `toolrepo_sha` did not describe. Reproduced: a
    committed template, then dirtied in the working tree without
    committing; the pinned SHA must still yield the COMMITTED bytes."""

    _init_git_repo(tmp_path)
    template_dir = tmp_path / "templates" / "agentreview-v2-target-pack"
    template_dir.mkdir(parents=True)
    (template_dir / "target-profile.v2.yaml").write_text("committed-content\n", encoding="utf-8")
    schema_dir = tmp_path / "schemas" / "agent-review" / "v2"
    schema_dir.mkdir(parents=True)
    (schema_dir / "agent-review.target-pack-manifest.v2.schema.json").write_text("{}\n", encoding="utf-8")
    sha = _commit_all(tmp_path)

    # Dirty the tracked template in the working tree, without committing.
    (template_dir / "target-profile.v2.yaml").write_text("DIRTY-UNCOMMITTED-content\n", encoding="utf-8")

    seed = load_seed_content_by_path_v2(toolrepo_root=tmp_path, toolrepo_sha=sha)
    assert seed[".aiops/target-profile.v2.yaml"] == b"committed-content\n"

    manifest = build_target_pack_manifest_v2(toolrepo_root=tmp_path, toolrepo_sha=sha, pack_version="0.1.0")
    profile_entry = next(e for e in manifest.generated_files if "target-profile" in e.path)
    import hashlib

    assert profile_entry.content_sha256 == hashlib.sha256(b"committed-content\n").hexdigest()


def test_an_untracked_schema_file_never_enters_schema_digests(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: `glob("*.schema.
    json")` against the working tree cannot distinguish a committed schema
    from an untracked one placed alongside it -- an untracked file silently
    changed the manifest digest. Reproduced: an untracked
    `*.schema.json` sitting in the schema directory."""

    _init_git_repo(tmp_path)
    template_dir = tmp_path / "templates" / "agentreview-v2-target-pack"
    template_dir.mkdir(parents=True)
    (template_dir / "target-profile.v2.yaml").write_text("seed\n", encoding="utf-8")
    schema_dir = tmp_path / "schemas" / "agent-review" / "v2"
    schema_dir.mkdir(parents=True)
    (schema_dir / "agent-review.target-pack-manifest.v2.schema.json").write_text("{}\n", encoding="utf-8")
    sha = _commit_all(tmp_path)

    (schema_dir / "untracked.schema.json").write_text('{"malicious": true}\n', encoding="utf-8")

    manifest = build_target_pack_manifest_v2(toolrepo_root=tmp_path, toolrepo_sha=sha, pack_version="0.1.0")
    assert "untracked.schema.json" not in manifest.schema_digests
    assert set(manifest.schema_digests) == {"agent-review.target-pack-manifest.v2.schema.json"}
