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


class TargetPackInstallError(ValueError):
    """Raised for an install/upgrade failure this module itself detects.
    Carries a stable `reason_code` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _atomic_write_v2(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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

    written: list[str] = []
    for file_action in plan.file_actions:
        if file_action.action in (PlannedActionV2.WRITE_NEW, PlannedActionV2.OVERWRITE_SAFE):
            content = seed_content_by_path[file_action.path]
            _atomic_write_v2(target_root / file_action.path, content)
            written.append(file_action.path)
        elif file_action.action is PlannedActionV2.REFUSE_DRIFT and file_action.path in force_overwrite_paths:
            content = seed_content_by_path[file_action.path]
            _atomic_write_v2(target_root / file_action.path, content)
            written.append(file_action.path)
        elif file_action.action is PlannedActionV2.MERGE_FENCED_BLOCK:
            _merge_fenced_block_v2(
                target_root / file_action.path, seed_content_by_path[file_action.path]
            )
            written.append(file_action.path)
        # WRITE_NEW for TARGET_OWNED falls through to the generic branch
        # above via the same action name -- SKIP_TARGET_OWNED and
        # NOOP_UNCHANGED intentionally do nothing.

    return tuple(written)


_FENCE_BEGIN_V2 = "# --- agent-review-v2:begin ---"
_FENCE_END_V2 = "# --- agent-review-v2:end ---"


def _merge_fenced_block_v2(path: Path, fenced_seed_content: bytes) -> None:
    """Replaces ONLY the text between `_FENCE_BEGIN_V2`/`_FENCE_END_V2` in
    `path` with `fenced_seed_content` (which itself is expected to already
    include the fence markers). Content outside the markers -- and the
    whole file, if the markers are not yet present -- is preserved/created
    exactly, never touched beyond the fenced region."""

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

    _atomic_write_v2(path, merged.encode("utf-8"))
