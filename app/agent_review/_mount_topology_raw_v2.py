"""Private raw mount-topology representation for the #262 typed authority.

Only ``target_pack_epoch_v2`` may import this product module. Tests may import
it to exercise representation mechanics directly, but ordinary product
consumers receive only the typed capability built by that owner.

This module owns observation, graph storage and traversal. It does not decide
K-DISJOINT policy, carrier overlap, target-domain acceptance, or acquisition
reason taxonomy beyond propagating the shared topology-unknown refusal.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from app.agent_review._target_pack_epoch_contract_v2 import (
    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2,
    TargetPackEpochError,
)


_MOUNT_ESCAPE_RE_V2 = re.compile(r"\\([0-7]{3})")


def _unescape_mountinfo_path_v2(value: str) -> str:
    return _MOUNT_ESCAPE_RE_V2.sub(lambda match: chr(int(match.group(1), 8)), value)


def _normalize_absolute_v2(path: str) -> str:
    return os.path.normpath(path) if path.startswith("/") else os.path.normpath("/" + path)


def _within_v2(candidate: str, ancestor: str) -> bool:
    if candidate == ancestor:
        return True
    return candidate.startswith(ancestor.rstrip("/") + "/")


class MountRecordV2(NamedTuple):
    mount_id: int
    parent_id: int
    device: int
    root: str
    mount_point: str
    filesystem_type: str


class TopologyQueryKindV2(str, Enum):
    POINT_LOOKUP = "point_lookup"
    VISIBLE_SUBTREE = "visible_subtree"


class TopologyQueryV2(NamedTuple):
    kind: TopologyQueryKindV2
    path: str


class TopologyQueryResolutionV2(NamedTuple):
    query: TopologyQueryV2
    governing_mount: MountRecordV2
    validated_frontier: tuple[MountRecordV2, ...]
    visible_descendants: tuple[MountRecordV2, ...]


class RawMountTopologyRepresentationV2:
    """Raw graph and the single typed topology-resolution implementation.

    The fields and raw traversal methods are intentionally available only in
    this private implementation module. The product owner captures an instance
    behind typed callables; it never stores this object on the consumer facade.
    """

    def __init__(self, records: tuple[MountRecordV2, ...]) -> None:
        self.records = records
        self.by_id: dict[int, MountRecordV2] = {}
        for record in records:
            if record.mount_id in self.by_id:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            self.by_id[record.mount_id] = record
        self.children: dict[int, list[MountRecordV2]] = {}
        for record in records:
            self.children.setdefault(record.parent_id, []).append(record)
        self._visible_cache: dict[int, bool] = {}

    @classmethod
    def observe(cls) -> "RawMountTopologyRepresentationV2":
        try:
            text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            ) from exc
        return cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> "RawMountTopologyRepresentationV2":
        records: list[MountRecordV2] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                before_separator, after_separator = line.split(" - ", 1)
                before = before_separator.split()
                major_text, minor_text = before[2].split(":", 1)
                records.append(MountRecordV2(
                    mount_id=int(before[0]),
                    parent_id=int(before[1]),
                    device=os.makedev(int(major_text), int(minor_text)),
                    root=_unescape_mountinfo_path_v2(before[3]),
                    mount_point=_unescape_mountinfo_path_v2(before[4]),
                    filesystem_type=after_separator.split()[0],
                ))
            except (IndexError, ValueError) as exc:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                ) from exc
        if not records:
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )
        return cls(tuple(records))

    def validate_relevant_chain_v2(self, record: MountRecordV2) -> None:
        seen = {record.mount_id}
        current = record
        while True:
            at_root = current.mount_point == "/"
            absent = current.parent_id not in self.by_id
            self_parent = current.parent_id == current.mount_id
            if absent or self_parent:
                if at_root:
                    return
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            parent = self.by_id[current.parent_id]
            if not _within_v2(current.mount_point, parent.mount_point):
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            current = parent
            if current.mount_id in seen:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            seen.add(current.mount_id)

    def _visible_root_v2(self) -> MountRecordV2:
        roots = [r for r in self.records if r.mount_point == "/"]
        if not roots:
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )
        base = [
            r for r in roots
            if r.parent_id not in self.by_id or r.parent_id == r.mount_id
        ]
        if len(base) != 1:
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )
        return self._climb_stack_v2(base[0], "/")

    def _climb_stack_v2(self, current: MountRecordV2, point: str) -> MountRecordV2:
        visited = {current.mount_id}
        while True:
            stacked = [
                child
                for child in self.children.get(current.mount_id, [])
                if child.mount_point == point and child.mount_id != current.mount_id
            ]
            if not stacked:
                return current
            if len(stacked) > 1:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            current = stacked[0]
            if current.mount_id in visited:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            visited.add(current.mount_id)

    def _governing_mount_raw_v2(self, path: str) -> MountRecordV2:
        path = _normalize_absolute_v2(path)
        current = self._visible_root_v2()
        prefix = ""
        for component in [component for component in path.split("/") if component]:
            prefix += "/" + component
            attached = [
                child
                for child in self.children.get(current.mount_id, [])
                if child.mount_point == prefix
            ]
            if not attached:
                continue
            if len(attached) > 1:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            current = self._climb_stack_v2(attached[0], prefix)
        self.validate_relevant_chain_v2(current)
        return current

    def _is_visible_raw_v2(self, record: MountRecordV2) -> bool:
        cached = self._visible_cache.get(record.mount_id)
        if cached is not None:
            return cached
        visible = (
            self._governing_mount_raw_v2(record.mount_point).mount_id == record.mount_id
        )
        self._visible_cache[record.mount_id] = visible
        return visible

    def _semantic_seeds_v2(
        self, query: TopologyQueryV2
    ) -> tuple[MountRecordV2, ...]:
        path = _normalize_absolute_v2(query.path)
        seeds = [record for record in self.records if _within_v2(path, record.mount_point)]
        if query.kind is TopologyQueryKindV2.VISIBLE_SUBTREE:
            seeds += [
                record
                for record in self.records
                if _within_v2(record.mount_point, path) and record not in seeds
            ]
        return tuple(seeds)

    def _dependency_closure_v2(
        self, seeds: tuple[MountRecordV2, ...]
    ) -> tuple[MountRecordV2, ...]:
        closed: dict[int, MountRecordV2] = {record.mount_id: record for record in seeds}
        pending = list(seeds)
        while pending:
            record = pending.pop()
            parent = self.by_id.get(record.parent_id)
            if parent is not None and parent.mount_id not in closed:
                closed[parent.mount_id] = parent
                pending.append(parent)
            for sibling in self.children.get(record.parent_id, []):
                if (
                    sibling.mount_point == record.mount_point
                    and sibling.mount_id not in closed
                ):
                    closed[sibling.mount_id] = sibling
                    pending.append(sibling)
            for child in self.children.get(record.mount_id, []):
                if (
                    child.mount_point == record.mount_point
                    and child.mount_id not in closed
                ):
                    closed[child.mount_id] = child
                    pending.append(child)
        return tuple(closed.values())

    def resolve_query_v2(self, query: TopologyQueryV2) -> TopologyQueryResolutionV2:
        frontier = self._dependency_closure_v2(self._semantic_seeds_v2(query))
        for record in frontier:
            self.validate_relevant_chain_v2(record)
        governing = self._governing_mount_raw_v2(query.path)
        descendants: tuple[MountRecordV2, ...] = ()
        if query.kind is TopologyQueryKindV2.VISIBLE_SUBTREE:
            prefix = _normalize_absolute_v2(query.path).rstrip("/") + "/"
            descendants = tuple(
                record
                for record in frontier
                if record.mount_point.startswith(prefix) and self._is_visible_raw_v2(record)
            )
        return TopologyQueryResolutionV2(query, governing, frontier, descendants)

    def governing_mount_v2(self, path: str) -> MountRecordV2:
        return self.resolve_query_v2(
            TopologyQueryV2(TopologyQueryKindV2.POINT_LOOKUP, path)
        ).governing_mount

    def visible_child_mounts_v2(self, path: str) -> tuple[MountRecordV2, ...]:
        return self.resolve_query_v2(
            TopologyQueryV2(TopologyQueryKindV2.VISIBLE_SUBTREE, path)
        ).visible_descendants

    def is_visible_v2(self, record: MountRecordV2) -> bool:
        return self.resolve_query_v2(
            TopologyQueryV2(TopologyQueryKindV2.POINT_LOOKUP, record.mount_point)
        ).governing_mount.mount_id == record.mount_id

    def project_v2(self, path: str) -> tuple[int, str]:
        path = _normalize_absolute_v2(path)
        governing = self.resolve_query_v2(
            TopologyQueryV2(TopologyQueryKindV2.POINT_LOOKUP, path)
        ).governing_mount
        remainder = path[len(governing.mount_point.rstrip("/")):]
        if not remainder or governing.mount_point == path:
            return (governing.device, governing.root)
        internal = _normalize_absolute_v2(governing.root.rstrip("/") + remainder)
        if not _within_v2(internal, governing.root):
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )
        return (governing.device, internal)
