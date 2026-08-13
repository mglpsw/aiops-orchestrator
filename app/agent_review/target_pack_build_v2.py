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
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)

BUILD_TEMPLATE_ROOT_MISSING_REASON_V2 = "target_pack_build_template_root_missing"
BUILD_TEMPLATE_SOURCE_MISSING_REASON_V2 = "target_pack_build_template_source_missing"


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


def _template_root_v2(toolrepo_root: Path) -> Path:
    return toolrepo_root / "templates" / "agentreview-v2-target-pack"


def build_target_pack_manifest_v2(
    *,
    toolrepo_root: Path,
    toolrepo_sha: str,
    pack_version: str,
) -> TargetPackManifestV2:
    template_root = _template_root_v2(toolrepo_root)
    if not template_root.is_dir():
        raise TargetPackBuildError(BUILD_TEMPLATE_ROOT_MISSING_REASON_V2)

    entries: list[GeneratedFileEntryV2] = []
    for source in _TEMPLATE_SOURCES_V2:
        full_path = template_root / source.template_relative_path
        if not full_path.is_file():
            raise TargetPackBuildError(BUILD_TEMPLATE_SOURCE_MISSING_REASON_V2)
        entries.append(
            GeneratedFileEntryV2(
                path=source.target_relative_path,
                ownership=source.ownership,
                content_sha256=_sha256_hex(full_path.read_bytes()),
            )
        )

    schema_dir = toolrepo_root / "schemas" / "agent-review" / "v2"
    schema_digests = {
        schema_path.name: _sha256_hex(schema_path.read_bytes())
        for schema_path in sorted(schema_dir.glob("*.schema.json"))
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


def load_seed_content_by_path_v2(*, toolrepo_root: Path) -> dict[str, bytes]:
    """The raw bytes for every template, keyed by TARGET install path --
    exactly how `InstallPlanV2.file_actions[].path` and `apply_install_
    plan_v2`'s `seed_content_by_path` parameter are keyed."""

    template_root = _template_root_v2(toolrepo_root)
    return {
        source.target_relative_path: (template_root / source.template_relative_path).read_bytes()
        for source in _TEMPLATE_SOURCES_V2
    }
