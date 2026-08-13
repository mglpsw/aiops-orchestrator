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

INSTALL_DRIFT_UNRESOLVED_REASON_V2 = "target_pack_install_drift_unresolved"
INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_install_path_escapes_target_root"


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
    module docstring."""

    unresolved_drift = set(plan.drifted_paths) - force_overwrite_paths
    if unresolved_drift:
        raise TargetPackInstallError(INSTALL_DRIFT_UNRESOLVED_REASON_V2)

    # Resolved ONCE per call. Every write below re-verifies against this
    # same value immediately before it happens -- see the module
    # docstring's "Symlink / path-escape containment" section.
    target_root_real = target_root.resolve(strict=False)

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
