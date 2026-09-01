"""Shared internal reason-code contract for target-pack epoch authorities."""

from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

TARGET_PACK_EPOCH_BUSY_REASON_V2 = "target_pack_epoch_busy"
TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2 = "target_pack_epoch_unavailable"
TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2 = "target_pack_epoch_target_subject_changed"
TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2 = "target_pack_epoch_capability_invalid"
TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2 = "target_pack_epoch_carrier_overlaps_target"
TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2 = (
    "target_pack_epoch_carrier_disjointness_unknown"
)

_TOPOLOGY_CAPABILITY_FORBIDDEN_API_NAMES_V2 = frozenset({
    "records",
    "children",
    "by_id",
    "raw",
    "raw_graph",
    "_raw",
    "_raw_graph",
    "_governing_mount_raw_v2",
    "_is_visible_raw_v2",
    "_visible_root_v2",
    "_climb_stack_v2",
    "validate_relevant_chain_v2",
    "_semantic_seeds_v2",
    "_dependency_closure_v2",
})


class TargetPackEpochError(ExpectedOperationalRefusalV2, ValueError):
    """A typed refusal while establishing or consuming a private epoch."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _within_v2(candidate: str, ancestor: str) -> bool:
    """Canonical containment relation for topology and K-DISJOINT."""

    if candidate == ancestor:
        return True
    return candidate.startswith(ancestor.rstrip("/") + "/")


# Preserve the established public import/pickle identity while sharing the
# exact class object with the private raw implementation.
TargetPackEpochError.__module__ = "app.agent_review.target_pack_epoch_v2"
