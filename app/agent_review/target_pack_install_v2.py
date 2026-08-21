"""FD-relative #203 target-pack file and receipt writers.

The canonical apply orchestration supplies one active exclusive K lease and
one held ``O_PATH`` target binding. This module consumes those capabilities;
it never acquires or releases a lease itself. It retains the existing
``resolve_within_target_root_v2`` containment authority, then performs
openat-style writes below the bound object. Each individual replacement is
temp-file-then-``os.replace``; this is not a multi-file crash-atomicity or
journal claim.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from app.agent_review.target_pack_epoch_v2 import TargetPackEpochLeaseV2, TargetPackTargetBindingV2
from app.agent_review.target_pack_manifest_v2 import (
    TargetPackManifestV2,
)
from app.agent_review.target_pack_plan_v2 import InstallPlanV2, PlanError, PlannedActionV2, resolve_within_target_root_v2
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


def _bound_root_proxy_v2(binding: TargetPackTargetBindingV2) -> Path:
    """A Linux procfs locator rooted in the held O_PATH object itself."""

    return Path(f"/proc/self/fd/{binding.fd}")


def _canonical_relative_write_path_v2(*, binding: TargetPackTargetBindingV2, path: str) -> Path:
    """Derive one FD-relative destination through the existing containment authority.

    The authority remains ``resolve_within_target_root_v2``.  The root passed
    to it is the held O_PATH object through procfs, not the caller's mutable
    target pathname; the later openat-style operations consume the resulting
    relative path from that same held descriptor.
    """

    try:
        bound_root = _bound_root_proxy_v2(binding).resolve(strict=True)
        resolved = resolve_within_target_root_v2(bound_root, bound_root / path)
        return resolved.relative_to(bound_root)
    except (OSError, RuntimeError, ValueError, PlanError) as exc:
        raise TargetPackInstallError(INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2) from exc


def _open_parent_directory_v2(*, binding: TargetPackTargetBindingV2, relative_parent: Path, create: bool) -> int:
    """Return an O_PATH directory FD reached only below the bound root."""

    current_fd = os.dup(binding.fd)
    os.set_inheritable(current_fd, False)
    directory_flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        for component in relative_parent.parts:
            if component in {"", ".", ".."}:
                raise TargetPackInstallError(INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2)
            try:
                child_fd = os.open(component, directory_flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o755, dir_fd=current_fd)
                child_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.set_inheritable(child_fd, False)
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _write_temp_then_replace_v2(*, parent_fd: int, final_name: str, content: bytes) -> None:
    """Atomic replacement entirely relative to one held parent directory FD."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    temporary_name: str | None = None
    temporary_fd: int | None = None
    try:
        for _ in range(16):
            candidate = f".{final_name}.{secrets.token_hex(12)}.tmp"
            try:
                temporary_fd = os.open(candidate, flags, 0o600, dir_fd=parent_fd)
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd is None or temporary_name is None:
            raise TargetPackInstallError(INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2)
        os.set_inheritable(temporary_fd, False)
        with os.fdopen(temporary_fd, "wb") as handle:
            temporary_fd = None
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temporary_name, final_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary_name = None
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass


def _atomic_write_v2(*, binding: TargetPackTargetBindingV2, path: str, content: bytes) -> None:
    relative = _canonical_relative_write_path_v2(binding=binding, path=path)
    if not relative.name or relative.name in {".", ".."}:
        raise TargetPackInstallError(INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2)
    parent_fd = _open_parent_directory_v2(binding=binding, relative_parent=relative.parent, create=True)
    try:
        _write_temp_then_replace_v2(parent_fd=parent_fd, final_name=relative.name, content=content)
    finally:
        os.close(parent_fd)


def apply_install_plan_v2(
    *,
    plan: InstallPlanV2,
    manifest: TargetPackManifestV2,
    seed_content_by_path: dict[str, bytes],
    lease: TargetPackEpochLeaseV2,
    target_binding: TargetPackTargetBindingV2,
    force_overwrite_paths: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Apply ``plan`` below the bound target object and return written paths.

    The caller has already passed the authorization gate.  Drift is still
    refused before any file write, and both the active exclusive lease and
    the binding's canonical subject must match ``plan.target_root_real``.
    """

    unresolved_drift = set(plan.drifted_paths) - force_overwrite_paths
    if unresolved_drift:
        raise TargetPackInstallError(INSTALL_DRIFT_UNRESOLVED_REASON_V2)

    if os.fsdecode(lease.canonical_target_subject) != plan.target_root_real:
        raise TargetPackInstallError(INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2)
    lease.require_exclusive_binding_v2(binding=target_binding, expected_target_root_real=plan.target_root_real)

    written: list[str] = []
    for file_action in plan.file_actions:
        if file_action.action in (PlannedActionV2.WRITE_NEW, PlannedActionV2.OVERWRITE_SAFE):
            content = seed_content_by_path[file_action.path]
            _atomic_write_v2(binding=target_binding, path=file_action.path, content=content)
            written.append(file_action.path)
        elif file_action.action is PlannedActionV2.REFUSE_DRIFT and file_action.path in force_overwrite_paths:
            content = seed_content_by_path[file_action.path]
            _atomic_write_v2(binding=target_binding, path=file_action.path, content=content)
            written.append(file_action.path)
        elif file_action.action is PlannedActionV2.MERGE_FENCED_BLOCK:
            _merge_fenced_block_v2(
                file_action.path,
                seed_content_by_path[file_action.path],
                binding=target_binding,
            )
            written.append(file_action.path)
        # WRITE_NEW for TARGET_OWNED falls through to the generic branch
        # above via the same action name -- SKIP_TARGET_OWNED and
        # NOOP_UNCHANGED intentionally do nothing.

    return tuple(written)


def write_receipt_v2(
    *,
    receipt: TargetInstallReceiptV2,
    expected_target_root_real: str,
    lease: TargetPackEpochLeaseV2,
    target_binding: TargetPackTargetBindingV2,
) -> None:
    """Persist a receipt under the exact same exclusive lease and binding."""

    import json

    if os.fsdecode(lease.canonical_target_subject) != expected_target_root_real:
        raise TargetPackInstallError(INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2)
    lease.require_exclusive_binding_v2(binding=target_binding, expected_target_root_real=expected_target_root_real)
    content = (json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _atomic_write_v2(binding=target_binding, path=RECEIPT_RELATIVE_PATH_V2, content=content)


_FENCE_BEGIN_V2 = "# --- agent-review-v2:begin ---"
_FENCE_END_V2 = "# --- agent-review-v2:end ---"


def _read_bound_file_or_empty_v2(*, binding: TargetPackTargetBindingV2, path: str) -> bytes:
    relative = _canonical_relative_write_path_v2(binding=binding, path=path)
    parent_fd = _open_parent_directory_v2(binding=binding, relative_parent=relative.parent, create=False)
    try:
        try:
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
        except FileNotFoundError:
            return b""
        try:
            os.set_inheritable(fd, False)
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                return handle.read()
        except BaseException:
            if fd >= 0:
                os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def _merge_fenced_block_v2(path: str, fenced_seed_content: bytes, *, binding: TargetPackTargetBindingV2) -> None:
    """Replaces ONLY the text between `_FENCE_BEGIN_V2`/`_FENCE_END_V2` in
    `path` with `fenced_seed_content` (which itself is expected to already
    include the fence markers). Content outside the markers -- and the
    whole file, if the markers are not yet present -- is preserved/created
    exactly, never touched beyond the fenced region."""

    try:
        existing = _read_bound_file_or_empty_v2(binding=binding, path=path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TargetPackInstallError(INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2) from exc
    new_block = fenced_seed_content.decode("utf-8")

    begin_index = existing.find(_FENCE_BEGIN_V2)
    end_index = existing.find(_FENCE_END_V2)
    if begin_index != -1 and end_index != -1 and end_index > begin_index:
        end_of_marker = end_index + len(_FENCE_END_V2)
        merged = existing[:begin_index] + new_block + existing[end_of_marker:]
    else:
        separator = "\n" if existing and not existing.endswith("\n") else ""
        merged = existing + separator + new_block

    _atomic_write_v2(binding=binding, path=path, content=merged.encode("utf-8"))
