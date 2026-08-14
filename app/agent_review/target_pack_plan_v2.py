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
PLAN_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2 = "target_pack_plan_rollout_exceeds_pack_capability"
PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_plan_path_escapes_target_root"

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
    must be `is_noop`.

    `target_root_real` -- `target_root.resolve(strict=False)` AT PLAN TIME
    -- exists so `apply_install_plan_v2` can detect `target_root` ITSELF
    having been swapped for a symlink between planning and applying (round
    5 adversarial finding: per-file symlink checks are meaningless if the
    very ROOT they are anchored to has already been redirected). See that
    function's own docstring."""

    file_actions: tuple[PlannedFileActionV2, ...]
    drifted_paths: tuple[str, ...]
    target_root_real: str

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


def resolve_within_target_root_v2(target_root_real: Path, path: Path) -> Path:
    """Resolve `path` and refuse if it escapes `target_root_real`.

    The read-side counterpart to `target_pack_install_v2._verify_write_
    target_within_root_v2`, which has protected the WRITE path since the
    round-4 adversarial review. Deliberately the SAME signature shape as
    that function (a pre-resolved root plus a full candidate path, not a
    root-plus-relative-suffix this function composes itself) and the SAME
    primitive (`Path.resolve(strict=False)` on the candidate, then
    `relative_to` against the root), so the pack has exactly ONE
    definition of "contained in `target_root`" rather than two that can
    drift apart -- and so every caller resolves `target_root` itself
    exactly ONCE per operation and threads that single value through every
    check, matching `apply_install_plan_v2`'s own `target_root_real =
    target_root.resolve(strict=False)` -> threaded into every `_atomic_
    write_v2` call, rather than re-resolving the root on every single
    file (round 2 finding, see below).

    `contracts_v2.RelativePath` proves a path STRING is well-formed (no
    `..`, no absolute/drive form, normalized spelling) -- it says nothing
    about what an EXISTING component on disk resolves to. Post-merge review
    debt (aiops-orchestrator#205, H1A-R1), confirmed and fixed: every
    pack-side READ (`run_doctor_v2`'s profile/receipt/target-owned reads,
    `compute_install_plan_v2`'s drift hashing, and `compute_target_pack_
    operation_plan_v2`'s TARGET_OWNED observation) composed
    `target_root / relative_path` and went straight to `is_file()` /
    `read_bytes()` / `read_text()`, all of which follow symlinks.
    Reproduced against the exact PR HEAD: with `.aiops/target-profile.v2.
    yaml` inside `target_root` symlinked to a file outside it,
    `run_doctor_v2` returned `is_healthy=True` while a `Path.read_text`/
    `read_bytes` spy recorded both reads resolving outside `target_root`.
    The write path had been hardened for exactly this class; the read path
    had not.

    Round 2 (independent re-review of the first fix): confirmed and fixed
    two further gaps. (1) `target_pack_doctor_v2._check_profile_v2`
    verified containment and then discarded the result, letting
    `load_target_profile_v2` independently re-derive `target_root /
    relative_path` and re-traverse it -- the check and the read were bound
    only by timing coincidence, not by construction. Reproduced
    deterministically (no real race needed): swapping the symlink to point
    outside `target_root` immediately after the passing containment check
    but before the read made the profile check report `status="present"`
    with a hash computed from content OUTSIDE `target_root`. Every caller
    now reads through the exact `Path` this function returns, never a
    separately re-derived one -- `_check_profile_v2` was the one holdout,
    now fixed the same way every other site already was. (2) this
    function used to resolve `target_root` itself, on every call --
    `target_pack_doctor_v2._check_receipt_v2`'s per-file loop called it
    once per target-owned entry, so `target_root`'s own resolution could
    legitimately differ between files checked in the same `run_doctor_v2`
    invocation if `target_root` itself were swapped mid-loop, the same
    class of root-swap concern `apply_install_plan_v2`'s own docstring
    names for the write side. Now takes an already-resolved
    `target_root_real`, resolved exactly once by each caller.

    A symlink whose target stays INSIDE `target_root` is deliberately
    ALLOWED -- containment, not symlink prohibition, is the property being
    enforced, and that keeps this check identical in meaning to the
    writer's.
    """

    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(target_root_real)
    except ValueError as exc:
        raise PlanError(PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2) from exc
    return resolved


def _read_on_disk_sha256_v2(target_root: Path, target_root_real: Path, relative_path: str) -> str | None:
    full_path = resolve_within_target_root_v2(target_root_real, target_root / relative_path)
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
    construction), never writes anything.

    This function resolves `target_root` ITSELF and never accepts a
    caller-supplied resolution. Round 4 finding (aiops-orchestrator#205,
    H1A-R1), confirmed by reproduction: an earlier version of the round-3
    fix took an optional `target_root_real` parameter so a caller could
    bind the plan to a root it had already resolved. That parameter was a
    containment-WIDENING vector -- passing any ANCESTOR of the real
    `target_root` (e.g. its parent directory) made every containment check
    inside this function evaluate against the wider root, so a symlink
    inside `target_root` pointing at a sibling directory passed
    containment and its content was read, while `plan.target_root_real`
    recorded the wider root. Reproduced directly: with the correct root
    the same layout is refused (`target_pack_plan_path_escapes_target_
    root`); with the parent passed as `target_root_real` the read of
    `<parent>/sibling/leak.txt` succeeded. A security primitive must not
    depend on its callers passing the right boundary, so the boundary is
    no longer a parameter at all.

    `InstallPlanV2.target_root_real` is therefore the single authoritative
    resolution for the whole operation: `compute_target_pack_operation_
    plan_v2` consumes it rather than resolving again, which is what keeps
    "one operation, one resolution" true without reintroducing a
    caller-supplied boundary."""

    actions: list[PlannedFileActionV2] = []
    drifted: list[str] = []

    recorded = previous_receipt.generated_file_hashes if previous_receipt is not None else {}
    # Resolved exactly once for the whole call, not once per entry -- see
    # `resolve_within_target_root_v2`'s own docstring, round 2.
    target_root_real = target_root.resolve(strict=False)

    for entry in manifest.generated_files:
        on_disk = _read_on_disk_sha256_v2(target_root, target_root_real, entry.path)
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

    return InstallPlanV2(
        file_actions=tuple(actions),
        drifted_paths=tuple(drifted),
        target_root_real=str(target_root_real),
    )


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


def validate_rollout_within_pack_capability_v2(*, requested: str, max_supported: str) -> None:
    """Refuses if `requested` (what the caller explicitly asked for, e.g.
    `--rollout` on the CLI) exceeds `max_supported`
    (`TargetPackManifestV2.max_supported_rollout_mode` -- the highest mode
    THIS pack version genuinely wires end-to-end). Distinct from `validate_
    rollout_ceiling_v2` above, which guards against a *resolution step*
    silently escalating beyond what the caller asked; this one guards
    against the caller asking for more than the pack can deliver AT ALL,
    independent of any resolution step. Adversarial review finding,
    confirmed and fixed: nothing previously called either function from
    `init`, so `--rollout shadow_full` was silently accepted against a
    pack version that ships no trusted-check integration whatsoever."""

    if rollout_mode_exceeds_pack_capability_v2(mode=requested, max_supported=max_supported):
        raise PlanError(PLAN_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2)


def rollout_mode_exceeds_pack_capability_v2(*, mode: str, max_supported: str) -> bool:
    """Non-raising predicate form of the same comparison `validate_
    rollout_within_pack_capability_v2` enforces at `init` time. Exists so
    `run_doctor_v2` can DIAGNOSE a receipt whose claimed `rollout_mode`
    exceeds what the CURRENT manifest can deliver -- e.g. a receipt
    written by a previous, more-capable pack version, now stale against a
    downgraded or reverted manifest -- without doctor needing to raise or
    import `PlanError` for what is a diagnosable state, not an input
    error. Round-1-of-the-post-external-review adversarial finding,
    confirmed and fixed: `run_doctor_v2` cross-checks a receipt's
    `pack_version`/`toolrepo_sha`/`target_profile_hash` against the
    manifest, but nothing checked `rollout_mode` against `max_supported_
    rollout_mode` -- a receipt claiming `shadow_full` against a manifest
    whose `max_supported_rollout_mode` is `shadow_minimal` reported
    `healthy=true`, asserting an operational state the pack cannot
    deliver, the same class of defect `init`'s own ceiling check (P2-4)
    was fixed for, just reachable through `doctor` instead."""

    if mode not in _ROLLOUT_ORDER_V2 or max_supported not in _ROLLOUT_ORDER_V2:
        return True
    return _ROLLOUT_ORDER_V2.index(mode) > _ROLLOUT_ORDER_V2.index(max_supported)
