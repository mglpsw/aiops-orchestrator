"""`#203` -- build a `TargetPackManifestV2` from this toolrepo's own
`templates/agentreview-v2-target-pack/` tree (issue #203).

This is the ONLY place ownership classification (`TargetPackFileOwnershipV2`)
AND the mapping between a template's SOURCE location (inside this toolrepo,
under `templates/agentreview-v2-target-pack/`) and its TARGET install
location (inside a consumer repository, e.g. `.aiops/target-profile.v2.yaml`)
are assigned -- a single source of truth, per the Execution-Ready
Engineering Specification `§7`. Every other module (plan, install, doctor)
consumes the resulting `TargetPackManifestV2`/seed content and never
re-derives either mapping from a path pattern of its own.

The two path spaces are deliberately kept distinct: the pack's own internal
layout (what ships inside `aiops-orchestrator`) is not required to mirror a
target's on-disk layout, and conflating them was a real bug caught while
smoke-testing `init` -- the profile template was landing at the target's
repo root instead of `.aiops/target-profile.v2.yaml`, where
`profile_loader_v2.load_target_profile_v2` actually looks for it.

## Pack material identity (spec rev.2 `§3`)

Every byte this module hashes or ships is read from the **immutable Git tree
at `toolrepo_sha`** (`git show <sha>:<path>`, `git ls-tree -r <sha>`) -- never
the working tree. Adversarial review finding, confirmed and fixed: this used
to call `Path.read_bytes()`/`glob()` directly against the working tree while
`toolrepo_sha` was independently resolved via `git rev-parse HEAD`. A dirty
tracked template therefore installed bytes the receipt's own `toolrepo_sha`
did not describe -- `toolrepo_sha=A` while the target received `A +
uncommitted`. Worse, `glob("*.schema.json")` picked up **untracked** files,
silently changing `schema_digests`. Reading from the pinned Git tree closes
both: `toolrepo_sha` now genuinely identifies the material installed, and
coverage of "every schema this pack version ships" is structural (the tree
listing), not a filesystem scan that can pick up stray files.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)

BUILD_TEMPLATE_ROOT_MISSING_REASON_V2 = "target_pack_build_template_root_missing"
BUILD_TEMPLATE_SOURCE_MISSING_REASON_V2 = "target_pack_build_template_source_missing"
BUILD_SCHEMA_TREE_UNREADABLE_REASON_V2 = "target_pack_build_schema_tree_unreadable"
BUILD_TOOLREPO_SHA_INVALID_SHAPE_REASON_V2 = "target_pack_build_toolrepo_sha_invalid_shape"

_TOOLREPO_SHA_HEX_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class TemplateSourceV2:
    """One template this pack version ships. `template_relative_path` is
    where the SEED content lives inside `templates/agentreview-v2-target-
    pack/` (this toolrepo). `target_relative_path` is where `init`/`upgrade`
    write it inside a TARGET repository -- these are independent path
    spaces, never assumed identical."""

    template_relative_path: str
    target_relative_path: str
    ownership: TargetPackFileOwnershipV2


# Every template this pack version ships. Adding a new templated file means
# adding one entry here -- `build_target_pack_manifest_v2` raises if a
# template file exists on disk with no matching entry, rather than silently
# ignoring it or silently defaulting its ownership.
_TEMPLATE_SOURCES_V2: tuple[TemplateSourceV2, ...] = (
    TemplateSourceV2(
        template_relative_path="target-profile.v2.yaml",
        target_relative_path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
    ),
)


class TargetPackBuildError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_TEMPLATE_TREE_PATH_V2 = "templates/agentreview-v2-target-pack"
_SCHEMA_TREE_PATH_V2 = "schemas/agent-review/v2"


def _require_valid_toolrepo_sha_v2(toolrepo_sha: str) -> None:
    if not _TOOLREPO_SHA_HEX_RE.fullmatch(toolrepo_sha):
        raise TargetPackBuildError(BUILD_TOOLREPO_SHA_INVALID_SHAPE_REASON_V2)


def _git_ls_tree_names_v2(*, toolrepo_root: Path, toolrepo_sha: str, tree_relative_path: str) -> tuple[str, ...]:
    """Every path under `tree_relative_path` as recorded in the Git tree at
    `toolrepo_sha` -- never a filesystem `glob`, which cannot distinguish a
    committed file from an untracked one sitting alongside it."""

    completed = subprocess.run(
        ["git", "-C", str(toolrepo_root), "ls-tree", "-r", "--name-only", toolrepo_sha, "--", tree_relative_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise TargetPackBuildError(BUILD_SCHEMA_TREE_UNREADABLE_REASON_V2)
    return tuple(line for line in completed.stdout.splitlines() if line)


def _git_show_bytes_v2(*, toolrepo_root: Path, toolrepo_sha: str, relative_path: str) -> bytes:
    """The bytes of `relative_path` as recorded in the immutable Git tree at
    `toolrepo_sha` -- never `Path.read_bytes()` against the working tree. See
    the module docstring's "Pack material identity" section."""

    completed = subprocess.run(
        ["git", "-C", str(toolrepo_root), "show", f"{toolrepo_sha}:{relative_path}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise TargetPackBuildError(BUILD_TEMPLATE_SOURCE_MISSING_REASON_V2)
    return completed.stdout


def build_target_pack_manifest_v2(
    *,
    toolrepo_root: Path,
    toolrepo_sha: str,
    pack_version: str,
) -> TargetPackManifestV2:
    _require_valid_toolrepo_sha_v2(toolrepo_sha)

    template_paths = _git_ls_tree_names_v2(
        toolrepo_root=toolrepo_root, toolrepo_sha=toolrepo_sha, tree_relative_path=_TEMPLATE_TREE_PATH_V2
    )
    if not template_paths:
        raise TargetPackBuildError(BUILD_TEMPLATE_ROOT_MISSING_REASON_V2)

    entries: list[GeneratedFileEntryV2] = []
    for source in _TEMPLATE_SOURCES_V2:
        relative_path = f"{_TEMPLATE_TREE_PATH_V2}/{source.template_relative_path}"
        content = _git_show_bytes_v2(toolrepo_root=toolrepo_root, toolrepo_sha=toolrepo_sha, relative_path=relative_path)
        entries.append(
            GeneratedFileEntryV2(
                path=source.target_relative_path,
                ownership=source.ownership,
                content_sha256=_sha256_hex(content),
            )
        )

    schema_paths = _git_ls_tree_names_v2(
        toolrepo_root=toolrepo_root, toolrepo_sha=toolrepo_sha, tree_relative_path=_SCHEMA_TREE_PATH_V2
    )
    schema_digests = {
        Path(schema_path).name: _sha256_hex(
            _git_show_bytes_v2(toolrepo_root=toolrepo_root, toolrepo_sha=toolrepo_sha, relative_path=schema_path)
        )
        for schema_path in sorted(p for p in schema_paths if p.endswith(".schema.json"))
    }

    return TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version=pack_version,
        toolrepo_sha=toolrepo_sha,
        generated_files=tuple(entries),
        schema_digests=schema_digests,
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        # Hardcoded for this slice: no trusted-check inventory, workflow
        # installer, or `ReviewReadinessV2` wiring ships yet, so
        # `shadow_full` (which the spec defines as that integration being
        # live) is not genuinely deliverable. A later slice that ships
        # that wiring must compute this from real installed capability,
        # not merely raise the constant.
        max_supported_rollout_mode="shadow_minimal",
    )


def load_seed_content_by_path_v2(*, toolrepo_root: Path, toolrepo_sha: str) -> dict[str, bytes]:
    """The raw bytes for every template, keyed by TARGET install path --
    exactly how `InstallPlanV2.file_actions[].path` and `apply_install_
    plan_v2`'s `seed_content_by_path` parameter are keyed. Read from the
    immutable Git tree at `toolrepo_sha`, same as `build_target_pack_
    manifest_v2` -- the seed a caller installs and the digest recorded in
    the manifest for it must always be read from the identical source, or
    the two could silently diverge."""

    _require_valid_toolrepo_sha_v2(toolrepo_sha)
    return {
        source.target_relative_path: _git_show_bytes_v2(
            toolrepo_root=toolrepo_root,
            toolrepo_sha=toolrepo_sha,
            relative_path=f"{_TEMPLATE_TREE_PATH_V2}/{source.template_relative_path}",
        )
        for source in _TEMPLATE_SOURCES_V2
    }
