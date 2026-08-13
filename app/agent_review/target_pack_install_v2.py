"""`#203` -- install/upgrade apply (issue #203).

The ONLY module in `agentreview-v2-target-pack` that writes to a target
repository. `apply_install_plan_v2` takes an already-computed, pure
`InstallPlanV2` (`target_pack_plan_v2.py`) and a manifest's seed content and
performs exactly the actions the plan names -- it decides nothing itself.
This mirrors `#201-C`'s own separation between decision (`_apply_required_
check_assessment_v2`) and construction (`_assemble_review_readiness_v2`):
the module that decides never writes, and the module that writes never
decides.

## Atomicity (spec `§10`, P-T10)

Every file write goes to a temp file in the same directory, then
`os.replace` (atomic on POSIX same-filesystem renames) into place -- a
process killed mid-`apply_install_plan_v2` leaves either the old file or the
fully-written new file at each path, never a partial write.

## Drift refusal (spec `§4.2`)

`apply_install_plan_v2` refuses outright -- writes nothing at all, for any
path -- if `plan.has_drift` and the caller did not pass the exact drifted
path(s) in `force_overwrite_paths`. This is enforced here, not only by the
CLI, so no future caller of this function can accidentally bypass it.

## Symlink / path-escape containment (spec `§10`, P-T2/P-T3)

Adversarial review finding, confirmed and fixed: `GeneratedFileEntryV2.path`
being a `contracts_v2.RelativePath` (no `..`, no absolute form) only proves
the STRING is well-formed -- it says nothing about what an EXISTING path
component on disk actually resolves to. Reproduced: with `.aiops` inside
`target_root` replaced by a symlink pointing outside `target_root`, the
pre-fix `_atomic_write_v2` happily followed it (via `Path.parent.mkdir`/
`tempfile.mkstemp(dir=...)`) and wrote pack-controlled content to an
arbitrary filesystem location.

`_verify_write_target_within_root_v2` resolves BOTH `target_root` and the
candidate write path (`Path.resolve(strict=False)`, which follows every
symlink an EXISTING path component actually is) and refuses if the
resolved write path is not contained in the resolved `target_root`. Called
from `_atomic_write_v2` itself -- the single writer -- immediately before
the write, not only once at plan time, so a symlink swapped in during the
window between `compute_install_plan_v2` and `apply_install_plan_v2` is
still caught (TOCTOU-aware, per the spec's own P-T3 entry). A genuine
concurrent-attacker race (swapping a real directory for a symlink in the
instant between this check and the following `mkdir`/`replace` calls) is a
known, accepted residual risk at this level of rigor -- the same class of
limit `#201-C`'s own merge gate declared honestly (`§6.4` of that slice's
spec) rather than solving with OS-level `O_NOFOLLOW` primitives this slice
does not need.

## `target_root` itself swapped between plan and apply (round 5)

The check above resolves `target_root` fresh at the start of `apply_
install_plan_v2` -- but if `target_root` ITSELF had already been replaced
by a symlink pointing elsewhere between `compute_install_plan_v2` and this
call, that "fresh" resolution just faithfully reports the attacker's
redirected location as ground truth, and every per-file check above passes
trivially against it. `InstallPlanV2.target_root_real` captures the
resolved root AT PLAN TIME; `apply_install_plan_v2` re-resolves `target_
root` at the START of its own call and refuses outright, writing nothing,
if the two disagree -- before any file-level check ever runs.

## Every write to a target repository goes through this module (round 5)

Adversarial review finding, confirmed and fixed: `scripts/agent-review-
target-pack-v2.py`'s `_cmd_init` used to write `.aiops/install-receipt.v2.
json` with a raw `Path.write_text`, bypassing every containment check
above entirely -- this module's own "ONLY module that writes" claim was
false. Reproduced: a pre-existing, valid `TARGET_OWNED` profile reached
through a symlinked `.aiops` (so `apply_install_plan_v2`'s own profile
write was `SKIP_TARGET_OWNED` -- no write attempted, no check triggered at
all) let the raw receipt write silently follow the same symlink, landing
`install-receipt.v2.json` entirely outside `target_root`, exit 0, no
refusal. `write_receipt_v2` is the fix: the CLI's only sanctioned way to
persist a receipt, routed through the exact same `_atomic_write_v2` (and
therefore the exact same symlink/root-identity containment) every other
write in this module already gets.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.agent_review.target_pack_manifest_v2 import (
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)
from app.agent_review.target_pack_plan_v2 import InstallPlanV2, PlannedActionV2
from app.agent_review.target_pack_receipt_v2 import RECEIPT_RELATIVE_PATH_V2, TargetInstallReceiptV2

INSTALL_DRIFT_UNRESOLVED_REASON_V2 = "target_pack_install_drift_unresolved"
INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_install_path_escapes_target_root"
INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2 = "target_pack_install_target_root_identity_changed"


class TargetPackInstallError(ValueError):
    """Raised for an install/upgrade failure this module itself detects.
    Carries a stable `reason_code` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _verify_write_target_within_root_v2(target_root_real: Path, path: Path) -> None:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(target_root_real)
    except ValueError as exc:
        raise TargetPackInstallError(INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2) from exc


def _atomic_write_v2(path: Path, content: bytes, *, target_root_real: Path) -> None:
    # Re-verified immediately before the write -- see the module docstring's
    # "Symlink / path-escape containment" section.
    _verify_write_target_within_root_v2(target_root_real, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkdir above can itself only ever traverse an EXISTING symlink (never
    # create a new escaping one), and the check just above already refused
    # any existing symlink that escapes -- so mkdir here is safe to run
    # unconditionally.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        # Adversarial review finding, confirmed and fixed: `tempfile.mkstemp`
        # creates the temp file `0600` (owner-only), and `os.replace`
        # preserves that mode onto the final path -- every pack-installed
        # file therefore landed more restrictive than an ordinary write
        # (`0644` under a typical umask), unlike anything else in this
        # target repository. Not a security hole (0600 is MORE restrictive,
        # never less), but a real correctness gap against ordinary-file
        # expectations, reproduced directly. Chmod'd on the TEMP path,
        # before the atomic rename, so the final path is never observable
        # at the wrong mode even momentarily.
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def apply_install_plan_v2(
    *,
    plan: InstallPlanV2,
    manifest: TargetPackManifestV2,
    target_root: Path,
    seed_content_by_path: dict[str, bytes],
    force_overwrite_paths: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Applies `plan` to `target_root`. Returns the tuple of paths actually
    written. Raises `TargetPackInstallError` (writing NOTHING) if any
    drifted path is not covered by `force_overwrite_paths` -- see the
    module docstring.

    Round 5 adversarial finding, confirmed and fixed: the per-file symlink
    check added in the previous round resolves `target_root` fresh at the
    START of this call -- but if `target_root` ITSELF had already been
    swapped for a symlink pointing elsewhere between `compute_install_
    plan_v2` and this call, that "fresh" resolution just faithfully
    reports the ATTACKER'S redirected location as the ground truth, and
    every subsequent per-file check passes trivially against it.
    Reproduced: swapping `target_root` for a symlink after planning, then
    applying, wrote the file into the symlink's target with no refusal at
    all -- the previous round's fix only ever protected an INTERMEDIATE
    path component, never the root itself.

    Fixed by comparing this call's own resolution of `target_root` against
    `plan.target_root_real` (captured at PLAN time) and refusing outright,
    before touching the filesystem at all, if they disagree -- the ground
    truth shifted between plan and apply, so nothing about this plan can
    be trusted to still apply to whatever `target_root` now is."""

    unresolved_drift = set(plan.drifted_paths) - force_overwrite_paths
    if unresolved_drift:
        raise TargetPackInstallError(INSTALL_DRIFT_UNRESOLVED_REASON_V2)

    target_root_real = target_root.resolve(strict=False)
    if str(target_root_real) != plan.target_root_real:
        raise TargetPackInstallError(INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2)

    written: list[str] = []
    for file_action in plan.file_actions:
        if file_action.action in (PlannedActionV2.WRITE_NEW, PlannedActionV2.OVERWRITE_SAFE):
            content = seed_content_by_path[file_action.path]
            _atomic_write_v2(target_root / file_action.path, content, target_root_real=target_root_real)
            written.append(file_action.path)
        elif file_action.action is PlannedActionV2.REFUSE_DRIFT and file_action.path in force_overwrite_paths:
            content = seed_content_by_path[file_action.path]
            _atomic_write_v2(target_root / file_action.path, content, target_root_real=target_root_real)
            written.append(file_action.path)
        elif file_action.action is PlannedActionV2.MERGE_FENCED_BLOCK:
            _merge_fenced_block_v2(
                target_root / file_action.path,
                seed_content_by_path[file_action.path],
                target_root_real=target_root_real,
            )
            written.append(file_action.path)
        # WRITE_NEW for TARGET_OWNED falls through to the generic branch
        # above via the same action name -- SKIP_TARGET_OWNED and
        # NOOP_UNCHANGED intentionally do nothing.

    return tuple(written)


def write_receipt_v2(
    *, target_root: Path, receipt: TargetInstallReceiptV2, expected_target_root_real: str
) -> None:
    """The CLI's only sanctioned way to persist a `TargetInstallReceiptV2`
    -- see the module docstring's "Every write to a target repository goes
    through this module" section. Routed through the exact same atomic,
    symlink/root-identity-checked `_atomic_write_v2` every other write in
    this module gets; never a raw `Path.write_text` in the CLI again.

    Adversarial review finding, confirmed and fixed (spec rev.2 §5.4,
    "mutation boundary and root identity" -- P2-C): `apply_install_plan_v2`
    binds its OWN writes to `plan.target_root_real`, captured at plan time,
    but this function previously re-resolved `target_root` independently,
    with no cross-check against any prior identity at all. A root swapped
    between `apply_install_plan_v2` completing and this call landed the
    receipt under a DIFFERENT root than the files it is supposed to
    describe -- reproduced directly: calling this function against a target
    root that had been replaced by a symlink wrote the receipt straight
    through it, with no refusal, no binding to the install it was
    persisting. `expected_target_root_real` is the SAME identity the plan
    was computed against (`InstallPlanV2.target_root_real`) and the SAME
    identity `apply_install_plan_v2` already verified before writing any
    file -- the receipt now closes with that identity, or not at all."""

    import json

    target_root_real = target_root.resolve(strict=False)
    if str(target_root_real) != expected_target_root_real:
        raise TargetPackInstallError(INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2)
    content = (json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _atomic_write_v2(target_root / RECEIPT_RELATIVE_PATH_V2, content, target_root_real=target_root_real)


_FENCE_BEGIN_V2 = "# --- agent-review-v2:begin ---"
_FENCE_END_V2 = "# --- agent-review-v2:end ---"


def _merge_fenced_block_v2(path: Path, fenced_seed_content: bytes, *, target_root_real: Path) -> None:
    """Replaces ONLY the text between `_FENCE_BEGIN_V2`/`_FENCE_END_V2` in
    `path` with `fenced_seed_content` (which itself is expected to already
    include the fence markers). Content outside the markers -- and the
    whole file, if the markers are not yet present -- is preserved/created
    exactly, never touched beyond the fenced region."""

    _verify_write_target_within_root_v2(target_root_real, path)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    new_block = fenced_seed_content.decode("utf-8")

    begin_index = existing.find(_FENCE_BEGIN_V2)
    end_index = existing.find(_FENCE_END_V2)
    if begin_index != -1 and end_index != -1 and end_index > begin_index:
        end_of_marker = end_index + len(_FENCE_END_V2)
        merged = existing[:begin_index] + new_block + existing[end_of_marker:]
    else:
        separator = "\n" if existing and not existing.endswith("\n") else ""
        merged = existing + separator + new_block

    _atomic_write_v2(path, merged.encode("utf-8"), target_root_real=target_root_real)
