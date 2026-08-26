"""Shared internal reason-code contract for target-pack epoch authorities."""

TARGET_PACK_EPOCH_BUSY_REASON_V2 = "target_pack_epoch_busy"
TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2 = "target_pack_epoch_unavailable"
TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2 = "target_pack_epoch_target_subject_changed"
TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2 = "target_pack_epoch_capability_invalid"
TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2 = "target_pack_epoch_carrier_overlaps_target"
TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2 = (
    "target_pack_epoch_carrier_disjointness_unknown"
)


class TargetPackEpochError(ValueError):
    """A typed refusal while establishing or consuming a private epoch."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


# Preserve the established public import/pickle identity while sharing the
# exact class object with the private raw implementation.
TargetPackEpochError.__module__ = "app.agent_review.target_pack_epoch_v2"
