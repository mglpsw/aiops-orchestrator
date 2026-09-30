from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.agent_review.target_pack_build_v2 import (
    BUILD_SCHEMA_TREE_UNREADABLE_REASON_V2,
    BUILD_TEMPLATE_ROOT_MISSING_REASON_V2,
    BUILD_TEMPLATE_SOURCE_MISSING_REASON_V2,
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


def test_countermodel_p19_recursive_schema_parity_git_vs_standalone(tmp_path: Path) -> None:
    """P19: Recursive schema tree scan achieves exact digest parity between Git-backed and standalone distributions."""
    import shutil

    git_toolrepo = tmp_path / "git_toolrepo"
    git_toolrepo.mkdir()
    _init_git_repo(git_toolrepo)

    template_dir = git_toolrepo / "templates" / "agentreview-v2-target-pack"
    template_dir.mkdir(parents=True)
    (template_dir / "target-profile.v2.yaml").write_text("profile-content\n", encoding="utf-8")

    schema_dir = git_toolrepo / "schemas" / "agent-review" / "v2"
    nested_schema_dir = schema_dir / "nested" / "sub"
    nested_schema_dir.mkdir(parents=True)

    (schema_dir / "agent-review.target-pack-manifest.v2.schema.json").write_text(
        '{"id": "manifest", "type": "object"}\n', encoding="utf-8"
    )
    (nested_schema_dir / "child.schema.json").write_text(
        '{"id": "child", "type": "string"}\n', encoding="utf-8"
    )

    sha = _commit_all(git_toolrepo)

    # 1. Build manifest from Git repo
    manifest_git = build_target_pack_manifest_v2(toolrepo_root=git_toolrepo, toolrepo_sha=sha, pack_version="0.1.0")

    # 2. Build manifest from standalone attestation-backed distribution (no .git)
    standalone_toolrepo = tmp_path / "standalone_toolrepo"
    shutil.copytree(template_dir, standalone_toolrepo / "templates" / "agentreview-v2-target-pack")
    shutil.copytree(schema_dir, standalone_toolrepo / "schemas" / "agent-review" / "v2")
    (standalone_toolrepo / ".source-commit").write_text(f"{sha}\n", encoding="utf-8")
    (standalone_toolrepo / ".toolrepo-sha").write_text(f"{sha}\n", encoding="utf-8")

    manifest_standalone = build_target_pack_manifest_v2(
        toolrepo_root=standalone_toolrepo, toolrepo_sha=sha, pack_version="0.1.0"
    )

    # Exact parity between Git and standalone
    assert manifest_git.schema_digests == manifest_standalone.schema_digests
    assert "agent-review.target-pack-manifest.v2.schema.json" in manifest_git.schema_digests
    assert "nested/sub/child.schema.json" in manifest_git.schema_digests
    assert len(manifest_git.schema_digests) == 2

    # Parity check on _resolve_toolrepo_sha between Git and standalone
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent_review_target_pack_cli_v2",
        REPO_ROOT / "scripts" / "agent-review-target-pack-v2.py",
    )
    cli_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli_mod)
    _resolve_toolrepo_sha = cli_mod._resolve_toolrepo_sha

    # Git mode resolves git HEAD
    assert _resolve_toolrepo_sha(git_toolrepo) == sha

    # Untracked or disagreeing attestation in Git repo raises TargetPackBuildError
    (git_toolrepo / ".source-commit").write_text("1" * 40 + "\n", encoding="utf-8")
    with pytest.raises(TargetPackBuildError):
        _resolve_toolrepo_sha(git_toolrepo)
    (git_toolrepo / ".source-commit").unlink()

    # Standalone mode resolves attestation
    assert _resolve_toolrepo_sha(standalone_toolrepo) == sha

    # Standalone mode with disagreeing attestations raises TargetPackBuildError
    (standalone_toolrepo / ".toolrepo-sha").write_text("2" * 40 + "\n", encoding="utf-8")
    with pytest.raises(TargetPackBuildError):
        _resolve_toolrepo_sha(standalone_toolrepo)



def test_countermodel_p20_template_symlink_escaping_toolrepo_is_rejected(tmp_path: Path) -> None:
    """P20: In a standalone distribution, template symlinks escaping toolrepo_root fail closed without reading outside bytes."""
    import shutil

    standalone = tmp_path / "standalone_escape"
    template_dir = standalone / "templates" / "agentreview-v2-target-pack"
    template_dir.mkdir(parents=True)
    schema_dir = standalone / "schemas" / "agent-review" / "v2"
    schema_dir.mkdir(parents=True)
    (schema_dir / "agent-review.target-pack-manifest.v2.schema.json").write_text("{}\n", encoding="utf-8")

    outside_file = tmp_path / "outside_secret.yaml"
    outside_file.write_text("evil_content: true\n", encoding="utf-8")

    # Symlink target-profile to outside_file
    symlink_path = template_dir / "target-profile.v2.yaml"
    symlink_path.symlink_to(outside_file)

    dummy_sha = "a" * 40
    (standalone / ".source-commit").write_text(f"{dummy_sha}\n", encoding="utf-8")
    (standalone / ".toolrepo-sha").write_text(f"{dummy_sha}\n", encoding="utf-8")

    with pytest.raises(TargetPackBuildError) as exc_manifest:
        build_target_pack_manifest_v2(toolrepo_root=standalone, toolrepo_sha=dummy_sha, pack_version="0.1.0")
    assert exc_manifest.value.reason_code == BUILD_TEMPLATE_SOURCE_MISSING_REASON_V2

    with pytest.raises(TargetPackBuildError) as exc_seed:
        load_seed_content_by_path_v2(toolrepo_root=standalone, toolrepo_sha=dummy_sha)
    assert exc_seed.value.reason_code == BUILD_TEMPLATE_SOURCE_MISSING_REASON_V2

    # Analogous test for schema symlink escaping root
    symlink_path.unlink()
    symlink_path.write_text("valid: true\n", encoding="utf-8")

    outside_schema = tmp_path / "outside.schema.json"
    outside_schema.write_text("{}\n", encoding="utf-8")
    schema_symlink = schema_dir / "escape.schema.json"
    schema_symlink.symlink_to(outside_schema)

    # Building manifest must reject or ignore the escaping schema symlink fail closed
    with pytest.raises(TargetPackBuildError) as exc_schema:
        build_target_pack_manifest_v2(toolrepo_root=standalone, toolrepo_sha=dummy_sha, pack_version="0.1.0")
    assert exc_schema.value.reason_code == BUILD_SCHEMA_TREE_UNREADABLE_REASON_V2


def test_countermodel_c8_nested_standalone_inside_parent_git_refuses_parent_authority(tmp_path: Path) -> None:
    """C8 (Finding S3): A standalone directory nested inside a parent Git repository must NOT use parent Git authority."""
    parent_repo = tmp_path / "parent_git_repo"
    parent_repo.mkdir()
    _init_git_repo(parent_repo)
    (parent_repo / "dummy.txt").write_text("dummy", encoding="utf-8")
    parent_sha = _commit_all(parent_repo)

    nested_standalone = parent_repo / "nested" / "standalone"
    nested_standalone.mkdir(parents=True)
    template_dir = nested_standalone / "templates" / "agentreview-v2-target-pack"
    template_dir.mkdir(parents=True)
    (template_dir / "target-profile.v2.yaml").write_text("mode: standalone\n", encoding="utf-8")
    schema_dir = nested_standalone / "schemas" / "agent-review" / "v2"
    schema_dir.mkdir(parents=True)
    (schema_dir / "agent-review.target-pack-manifest.v2.schema.json").write_text("{}\n", encoding="utf-8")

    # Parent repo contains parent_sha, but nested_standalone is NOT a git repo root.
    from app.agent_review.target_pack_build_v2 import _is_git_tree_accessible_v2
    assert not _is_git_tree_accessible_v2(toolrepo_root=nested_standalone, toolrepo_sha=parent_sha)

    # Standalone mode requires valid matching attestations
    (nested_standalone / ".source-commit").write_text(f"{parent_sha}\n", encoding="utf-8")
    (nested_standalone / ".toolrepo-sha").write_text(f"{parent_sha}\n", encoding="utf-8")
    manifest = build_target_pack_manifest_v2(
        toolrepo_root=nested_standalone, toolrepo_sha=parent_sha, pack_version="0.1.0"
    )
    assert manifest.toolrepo_sha == parent_sha


def test_countermodel_c9_standalone_attestation_symlink_refused(tmp_path: Path) -> None:
    """C9 (Finding S1): Symlinked attestation files in standalone mode are refused fail-closed."""
    standalone = tmp_path / "standalone_symlink_attest"
    standalone.mkdir()
    outside_sha_file = tmp_path / "outside_sha.txt"
    sha = "f" * 40
    outside_sha_file.write_text(f"{sha}\n", encoding="utf-8")

    (standalone / ".source-commit").symlink_to(outside_sha_file)
    (standalone / ".toolrepo-sha").write_text(f"{sha}\n", encoding="utf-8")

    from app.agent_review.target_pack_build_v2 import _resolve_attested_toolrepo_sha
    assert _resolve_attested_toolrepo_sha(standalone) is None
