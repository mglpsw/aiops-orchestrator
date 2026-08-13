"""`#203` -- pure install/upgrade plan computation (issue #203).

`compute_install_plan_v2` decides WHAT would happen to a target repository
from `(TargetPackManifestV2, current on-disk state, previous receipt)`
without touching the filesystem -- spec `§4.1`. It is a pure, total
function: same inputs, same plan, every time. `target_pack_install_v2.py`
is the only module that ever applies a plan (writes files); this module
never does.

## The three-case drift table (spec `§4.2`), made exhaustive here

For every `UPSTREAM_GENERATED` entry in the manifest:

1. no previous receipt recorded for this path -> `WRITE_NEW`;
2. on-disk hash == previous receipt's recorded hash for this path
   -> `OVERWRITE_SAFE` (the target never touched it);
3. on-disk hash != previous receipt's recorded hash for this path
   -> `REFUSE_DRIFT` (the target edited a file the pack thinks it owns).

For every `TARGET_OWNED` entry: `WRITE_NEW` only if the path does not exist
yet (first `init`); otherwise `SKIP_TARGET_OWNED` -- never read, hashed, or
compared again after that.

For every `MERGED_DECLARATIVE` entry: `MERGE_FENCED_BLOCK` always -- only
the pack's own fenced region (`# --- agent-review-v2:begin ---` / `:end`)
is ever planned for change; content outside the markers is preserved
byte-for-byte and never appears in the plan's diff.

No fourth case exists; `_classify_action_v2` raises if it ever computes one,
rather than silently defaulting to a write.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)
from app.agent_review.target_pack_receipt_v2 import TargetInstallReceiptV2

PLAN_UNKNOWN_OWNERSHIP_REASON_V2 = "target_pack_plan_unknown_ownership_class"
PLAN_ROLLOUT_CEILING_EXCEEDED_REASON_V2 = "target_pack_plan_rollout_ceiling_exceeded"

_ROLLOUT_ORDER_V2 = ("off", "shadow_minimal", "shadow_full")

_FENCE_BEGIN_V2 = "# --- agent-review-v2:begin ---"
_FENCE_END_V2 = "# --- agent-review-v2:end ---"


class PlannedActionV2(str, Enum):
    WRITE_NEW = "write_new"
    OVERWRITE_SAFE = "overwrite_safe"
    REFUSE_DRIFT = "refuse_drift"
    SKIP_TARGET_OWNED = "skip_target_owned"
    MERGE_FENCED_BLOCK = "merge_fenced_block"
    NOOP_UNCHANGED = "noop_unchanged"


class PlanError(ValueError):
    """Raised for a plan-computation failure this module itself detects.
    Carries a stable `reason_code` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PlannedFileActionV2:
    path: str
    ownership: TargetPackFileOwnershipV2
    action: PlannedActionV2
    seed_content_sha256: str
    on_disk_sha256: str | None
    previously_recorded_sha256: str | None


@dataclass(frozen=True)
class InstallPlanV2:
    """The full, pure result of `compute_install_plan_v2`. `is_noop` is
    true iff every entry resolved to `NOOP_UNCHANGED`/`SKIP_TARGET_OWNED`
    -- the property `target_pack_plan_v2`'s idempotence tests assert
    directly: computing a plan against a target a plan was JUST applied to
    must be `is_noop`."""

    file_actions: tuple[PlannedFileActionV2, ...]
    drifted_paths: tuple[str, ...]

    @property
    def is_noop(self) -> bool:
        return not self.drifted_paths and all(
            action.action in (PlannedActionV2.NOOP_UNCHANGED, PlannedActionV2.SKIP_TARGET_OWNED)
            for action in self.file_actions
        )

    @property
    def has_drift(self) -> bool:
        return bool(self.drifted_paths)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_on_disk_sha256_v2(target_root: Path, relative_path: str) -> str | None:
    full_path = target_root / relative_path
    if not full_path.is_file():
        return None
    return _sha256_hex(full_path.read_bytes())


def _classify_action_v2(
    *,
    entry: GeneratedFileEntryV2,
    on_disk_sha256: str | None,
    previously_recorded_sha256: str | None,
) -> PlannedActionV2:
    if entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED:
        return PlannedActionV2.WRITE_NEW if on_disk_sha256 is None else PlannedActionV2.SKIP_TARGET_OWNED

    if entry.ownership is TargetPackFileOwnershipV2.MERGED_DECLARATIVE:
        return PlannedActionV2.MERGE_FENCED_BLOCK

    if entry.ownership is TargetPackFileOwnershipV2.UPSTREAM_GENERATED:
        if on_disk_sha256 is None:
            return PlannedActionV2.WRITE_NEW
        if on_disk_sha256 == entry.content_sha256:
            # Already exactly the seed content this pack version would
            # write -- nothing to do, not even a safe rewrite.
            return PlannedActionV2.NOOP_UNCHANGED
        if previously_recorded_sha256 is not None and on_disk_sha256 == previously_recorded_sha256:
            return PlannedActionV2.OVERWRITE_SAFE
        return PlannedActionV2.REFUSE_DRIFT

    raise PlanError(PLAN_UNKNOWN_OWNERSHIP_REASON_V2)  # pragma: no cover - exhaustive enum guarded above


def compute_install_plan_v2(
    *,
    manifest: TargetPackManifestV2,
    target_root: Path,
    previous_receipt: TargetInstallReceiptV2 | None,
) -> InstallPlanV2:
    """Pure. Reads `target_root`'s current file CONTENT to compute
    on-disk hashes (this is read-only inspection, not mutation -- the same
    boundary `run_doctor_v2` depends on to prove itself read-only by
    construction), never writes anything."""

    actions: list[PlannedFileActionV2] = []
    drifted: list[str] = []

    recorded = previous_receipt.generated_file_hashes if previous_receipt is not None else {}

    for entry in manifest.generated_files:
        on_disk = _read_on_disk_sha256_v2(target_root, entry.path)
        previously_recorded = recorded.get(entry.path)
        action = _classify_action_v2(
            entry=entry, on_disk_sha256=on_disk, previously_recorded_sha256=previously_recorded
        )
        if action is PlannedActionV2.REFUSE_DRIFT:
            drifted.append(entry.path)
        actions.append(
            PlannedFileActionV2(
                path=entry.path,
                ownership=entry.ownership,
                action=action,
                seed_content_sha256=entry.content_sha256,
                on_disk_sha256=on_disk,
                previously_recorded_sha256=previously_recorded,
            )
        )

    return InstallPlanV2(file_actions=tuple(actions), drifted_paths=tuple(drifted))


def validate_rollout_ceiling_v2(*, requested: str, resolved: str) -> None:
    """Refuses if `resolved` (what an install/upgrade is ABOUT to write)
    would exceed `requested` (what the caller explicitly asked for) --
    spec `§8`'s "no silent ceiling promotion" invariant. `off` < `shadow_
    minimal` < `shadow_full`; nothing above `shadow_full` exists, and
    nothing in this module or its callers has any code path toward
    required/default/primary branch-protection promotion -- that capability
    does not exist in this codebase at all, not merely unused here."""

    if requested not in _ROLLOUT_ORDER_V2 or resolved not in _ROLLOUT_ORDER_V2:
        raise PlanError(PLAN_ROLLOUT_CEILING_EXCEEDED_REASON_V2)
    if _ROLLOUT_ORDER_V2.index(resolved) > _ROLLOUT_ORDER_V2.index(requested):
        raise PlanError(PLAN_ROLLOUT_CEILING_EXCEEDED_REASON_V2)
