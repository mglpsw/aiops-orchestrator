"""Single authority for reading target-owned source files and admitting their parsed structure (AR-V1-C2, #375).

Two primitives, both used by every v1 source reader (repo profile, contract sources, MethodGuidance sidecar and
sources, declared artifacts):

``acquire_source`` -- ``ExplicitTrustedRoot + relative path -> SourceAcquisition``.
    The trusted root is opened once; every further component is opened relative to the previous directory
    descriptor with ``O_NOFOLLOW`` (directories also ``O_DIRECTORY``), so a link on ANY component is refused and the
    object that is size-checked (``fstat`` on the opened descriptor) is the object that is read. There is no
    resolve-then-open window and no separate existence probe: absence is decided by the SAME open that would read the
    bytes, and only ``ENOENT`` means absent. ``O_NONBLOCK`` keeps a FIFO from blocking the open; a file whose
    ``st_nlink`` is not 1 is refused because a second name may belong to an inode outside the tree. The read is
    bounded; an oversize file is a typed state, never truncated. A platform without ``O_NOFOLLOW`` or
    descriptor-relative open fails closed (``UNSUPPORTED``).

``scan_structure`` -- iterative, memoised admission of an already-parsed document.
    Cycle detection, depth bound and a bound on the EXPANDED node count (a shared sub-structure counts once per
    reference), without recursion proportional to the document depth. It must run before anything that expands the
    document (serialisation, redaction, model dump): YAML aliases make a tiny document an exponentially large tree.

``GenuinelyAbsent != PresentButInvalidOrUnsafe``: only ``ABSENT`` may be read as "nothing declared".
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Any

SOURCE_STATE_ABSENT = "ABSENT"
SOURCE_STATE_PRESENT = "PRESENT"
SOURCE_STATE_UNSAFE = "UNSAFE"  # a link on the path, a hard link, a non-regular file, a non-directory parent
SOURCE_STATE_UNREADABLE = "UNREADABLE"  # presence cannot be established or the bytes cannot be read (EACCES, EIO, ...)
SOURCE_STATE_OVERSIZE = "OVERSIZE"
SOURCE_STATE_UNSUPPORTED = "UNSUPPORTED"  # platform cannot give the guarantees above

# Design values (see docs/engineering/AGENT_REVIEW_V1_C2_SOURCE_BOUNDARY.md), validated against the real AgentEscala
# sources (<= 21 KiB, <= 493 expanded nodes, depth <= 4) and the MethodGuidance sidecar grammar (<= ~1.2 MiB).
# PyYAML is pure Python and linear in bytes (measured ~5.8 s / 142 MB at 1.3 MB): the byte cap is the only
# pre-parse bound, so it is a cost cap, NOT a proof that the parser is resource-bounded.
YAML_SOURCE_MAX_BYTES = 2 * 1024 * 1024
METHOD_DOCUMENT_MAX_BYTES = 4 * 1024 * 1024  # method source documents are hashed and searched, never parsed
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024  # declared artifacts: bounds special/huge files; never a truncation policy
STRUCTURE_MAX_DEPTH = 64
STRUCTURE_MAX_NODES = 50_000
STRUCTURE_MAX_NODES_ARTIFACT = 500_000

STRUCTURE_OK = None
STRUCTURE_CYCLE = "cycle"
STRUCTURE_DEPTH = "depth"
STRUCTURE_NODES = "nodes"


@dataclass(frozen=True)
class SourceAcquisition:
    state: str
    data: bytes | None = None
    reason: str = ""


def acquire_source(root: Any, relative: str, *, max_bytes: int) -> SourceAcquisition:
    """Read ``relative`` (a POSIX path below the trusted ``root``) without following links. Never raises."""
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        return SourceAcquisition(SOURCE_STATE_UNSUPPORTED, reason="platform_without_nofollow_or_dir_fd")
    parts = relative.split("/") if isinstance(relative, str) else []
    if not parts or any(part in ("", ".", "..") or "\0" in part for part in parts):
        return SourceAcquisition(SOURCE_STATE_UNSAFE, reason="path_invalid")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    directory_fd = -1
    file_fd = -1
    try:
        try:
            directory_fd = os.open(root, directory_flags)
        except OSError:
            return SourceAcquisition(SOURCE_STATE_UNREADABLE, reason="root_unavailable")
        for part in parts[:-1]:
            try:
                next_fd = os.open(part, directory_flags | os.O_NOFOLLOW, dir_fd=directory_fd)
            except FileNotFoundError:
                return SourceAcquisition(SOURCE_STATE_ABSENT)
            except PermissionError:
                return SourceAcquisition(SOURCE_STATE_UNREADABLE, reason="permission_denied")
            except OSError:  # ELOOP / ENOTDIR: a link or a non-directory where a directory must be
                return SourceAcquisition(SOURCE_STATE_UNSAFE, reason="parent_not_a_plain_directory")
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            file_fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd
            )
        except FileNotFoundError:
            return SourceAcquisition(SOURCE_STATE_ABSENT)
        except PermissionError:
            return SourceAcquisition(SOURCE_STATE_UNREADABLE, reason="permission_denied")
        except OSError:  # ELOOP: the final component is a link (dangling or not)
            return SourceAcquisition(SOURCE_STATE_UNSAFE, reason="symlink")
        status = os.fstat(file_fd)
        if not stat.S_ISREG(status.st_mode):
            return SourceAcquisition(SOURCE_STATE_UNSAFE, reason="not_a_regular_file")
        if status.st_nlink != 1:
            return SourceAcquisition(SOURCE_STATE_UNSAFE, reason="multiple_hard_links")
        if status.st_size > max_bytes:
            return SourceAcquisition(SOURCE_STATE_OVERSIZE, reason="size_limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            block = os.read(file_fd, min(remaining, 1024 * 1024))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            return SourceAcquisition(SOURCE_STATE_OVERSIZE, reason="size_limit")
        return SourceAcquisition(SOURCE_STATE_PRESENT, data=data)
    except (OSError, ValueError):
        return SourceAcquisition(SOURCE_STATE_UNREADABLE, reason="io_error")
    finally:
        for descriptor in (file_fd, directory_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


@dataclass(frozen=True)
class StructureVerdict:
    limit: str | None  # None, "cycle", "depth" or "nodes"
    has_non_string_key: bool


def scan_structure(document: Any, *, max_depth: int = STRUCTURE_MAX_DEPTH, max_nodes: int = STRUCTURE_MAX_NODES) -> StructureVerdict:
    """Iterative post-order admission of a parsed document (dict / list / scalars). Never recurses on depth.

    ``nodes`` is the expanded count with multiplicity: every mapping entry counts its key plus its value subtree, a
    container counts itself, and a container referenced N times contributes N times (memoised by ``id``, so the work
    is linear in UNIQUE containers). ``depth`` is the height of the expanded tree. A container met while it is still
    on the current path is a ``cycle``; a shared container that is merely referenced twice is not.
    """
    size: dict[int, int] = {}
    height: dict[int, int] = {}
    on_path: set[int] = set()
    non_string_key = False
    root_is_container = isinstance(document, (dict, list))
    if not root_is_container:
        return StructureVerdict(STRUCTURE_OK, False)

    def child_values(node: Any) -> list[Any]:
        return list(node.values()) if isinstance(node, dict) else list(node)

    stack: list[tuple[Any, bool]] = [(document, False)]
    while stack:
        node, exiting = stack.pop()
        node_id = id(node)
        if exiting:
            on_path.discard(node_id)
            total = 1
            tallest = 0
            if isinstance(node, dict):
                total += len(node)  # one node per key
            for child in child_values(node):
                if isinstance(child, (dict, list)):
                    total += size[id(child)]
                    tallest = max(tallest, height[id(child)])
                else:
                    total += 1
            node_height = 1 + tallest
            if total > max_nodes:
                return StructureVerdict(STRUCTURE_NODES, non_string_key)
            if node_height > max_depth:
                return StructureVerdict(STRUCTURE_DEPTH, non_string_key)
            size[node_id] = total
            height[node_id] = node_height
            continue
        if node_id in size:
            continue
        if node_id in on_path:
            return StructureVerdict(STRUCTURE_CYCLE, non_string_key)
        on_path.add(node_id)
        stack.append((node, True))
        if isinstance(node, dict):
            if any(not isinstance(key, str) for key in node):
                non_string_key = True
        for child in child_values(node):
            if isinstance(child, (dict, list)):
                if id(child) in on_path:
                    return StructureVerdict(STRUCTURE_CYCLE, non_string_key)
                if id(child) not in size:
                    stack.append((child, False))
    return StructureVerdict(STRUCTURE_OK, non_string_key)
