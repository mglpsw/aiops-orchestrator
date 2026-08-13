"""`#203` -- the target pack manifest (issue #203, child of tracker #199).

`TargetPackManifestV2` is the compiled, versioned description of what one
toolrepo release of `agentreview-v2-target-pack` CAN install: the set of
files it may generate, each classified by ownership (`#203`'s own "safe file
ownership" invariant), the schema digests this pack version pins, and the
minimum engine contract version a target must accept.

It is built once per toolrepo release (`compute_target_pack_manifest_v2`)
and consumed, read-only, by every target's `init`/`doctor`/`validate`/
`install-workflows`/`upgrade`/`rollback` invocation. Nothing in this module
writes to a target repository -- see `target_pack_plan_v2.py` for the
install/upgrade PLAN this manifest feeds, and `target_pack_install_v2.py`
for the only code that actually touches a target's filesystem.

## Why this is a NEW contract, not a reuse of `TargetProfileV2`

`TargetProfileV2` (`profile_loader_v2.py`, `#85`) describes a target's own
review configuration -- what it wants reviewed, by which policy. This
manifest describes the OPPOSITE direction: what one pack VERSION is capable
of installing, independent of any single target. Collapsing the two would
make a pack-version upgrade indistinguishable from a target reconfiguring
its own review policy, which `#203`'s own drift/ownership model (`§4.2`,
`§7` of the Execution-Ready Engineering Specification) depends on keeping
separate.

## Frozen contracts untouched

`contracts_v2.py` and every schema under `schemas/agent-review/v2/` are
NOT touched by this module -- `#203 MUST NEVER CREATE AUTHORITY, FORK THE
ENGINE, OR SILENTLY PROMOTE ROLLOUT`, restated from `#201-C`'s own boundary.
This manifest is purely additive, non-wire in the sense that it is never
accepted as input by any review/readiness function -- it is consumed only
by the pack's own install/upgrade/doctor code.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal, Mapping

from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    GitSha,
    RelativePath,
    SafeIdentifier,
    SafeText,
    Sha256,
)

TARGET_PACK_MANIFEST_SCHEMA_ID_V2 = "agent-review.target-pack-manifest.v2"

MANIFEST_DUPLICATE_GENERATED_PATH_REASON_V2 = "target_pack_manifest_duplicate_generated_path"
MANIFEST_EMPTY_SCHEMA_DIGESTS_REASON_V2 = "target_pack_manifest_empty_schema_digests"


class TargetPackFileOwnershipV2(str, Enum):
    """`#203`'s "safe file ownership" classes (spec `§7`). Determines what
    `target_pack_plan_v2.compute_install_plan_v2` is ever allowed to do to a
    given generated path -- see that module's own docstring for the
    per-class write/diff/preserve rules. This enum is the SOLE source of
    truth for that classification; no CLI command re-derives it from a path
    pattern."""

    UPSTREAM_GENERATED = "upstream_generated"
    TARGET_OWNED = "target_owned"
    MERGED_DECLARATIVE = "merged_declarative"


class GeneratedFileEntryV2(ContractV2Model):
    """One file this pack version may place in a target repository.
    `content_sha256` is the digest of the SEED/TEMPLATE content this pack
    version ships for that path -- for `TARGET_OWNED` paths, only ever used
    once, at `init`; for `UPSTREAM_GENERATED`, the digest `target_pack_plan_
    v2` compares the target's on-disk file against to decide safe-overwrite
    vs. drift (spec `§4.2`)."""

    path: RelativePath
    ownership: TargetPackFileOwnershipV2
    content_sha256: Sha256


class TargetPackManifestV2(ContractV2Model):
    """The compiled description of one toolrepo release's install
    capability. See the module docstring for why this is not
    `TargetProfileV2`."""

    schema_id: Literal["agent-review.target-pack-manifest.v2"]
    schema_version: Literal[2]
    pack_version: SafeText
    toolrepo_sha: GitSha
    generated_files: tuple[GeneratedFileEntryV2, ...] = Field(min_length=1)
    schema_digests: Mapping[str, Sha256] = Field(min_length=1, json_schema_extra={"additionalProperties": False})
    required_capabilities: tuple[SafeIdentifier, ...]
    min_engine_contract_version: Literal[2]

    @model_validator(mode="after")
    def validate_generated_files_are_unique(self) -> TargetPackManifestV2:
        paths = [entry.path for entry in self.generated_files]
        if len(paths) != len(set(paths)):
            raise ValueError(MANIFEST_DUPLICATE_GENERATED_PATH_REASON_V2)
        return self


def _canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_target_pack_manifest_bytes_v2(manifest: TargetPackManifestV2) -> bytes:
    """Canonical bytes for `compute_target_pack_manifest_digest_v2` -- every
    validated field, same canonical-JSON discipline `profile_loader_v2.
    canonical_target_profile_bytes_v2` already uses (sorted keys, no
    whitespace, UTF-8)."""

    return _canonical_json_bytes_v2(manifest.model_dump(mode="json"))


def compute_target_pack_manifest_digest_v2(manifest: TargetPackManifestV2) -> str:
    return hashlib.sha256(canonical_target_pack_manifest_bytes_v2(manifest)).hexdigest()
