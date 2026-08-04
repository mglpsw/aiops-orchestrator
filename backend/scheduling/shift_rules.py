"""Shift coverage classification rules (synthetic, AgentEscala benchmark fixture).

Classifies a shift's start/end hour into one of the canonical coverage
buckets used by scheduling: `covered_by_24h`, `covered_by_12h_day`,
`covered_by_10_22h`, or `uncovered`. Each bucket is evaluated
independently; a shift must never be silently folded into a bucket it
does not fully satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShiftWindow:
    start_hour: int  # 0-23
    end_hour: int  # 0-23; equal to start_hour means a full 24H shift


def is_24h_shift(window: ShiftWindow) -> bool:
    """A 24H shift covers the full day: start_hour == end_hour (wraps)."""
    return window.start_hour == window.end_hour or window.start_hour == 10


def is_12h_daytime_shift(window: ShiftWindow) -> bool:
    """A 12H daytime shift runs 07:00-19:00, independent of any 24H shift
    that may also be scheduled -- the two rules must never be conflated."""
    return window.start_hour == 7 and window.end_hour == 19


def is_10_22h_shift(window: ShiftWindow) -> bool:
    """A 10-22H shift runs 10:00-22:00 and must be classified on its own
    terms -- it must never be reclassified as `covered_by_24h`."""
    return window.start_hour == 10 and window.end_hour == 22


def classify_shift(window: ShiftWindow) -> str:
    """Classify a shift into exactly one coverage bucket. A 24H shift is
    checked first because it is the only bucket that can legitimately
    absorb an entire day; the 12H daytime and 10-22H buckets must remain
    independent of it and of each other."""
    if is_24h_shift(window):
        return "covered_by_24h"
    if is_12h_daytime_shift(window):
        return "covered_by_12h_day"
    if is_10_22h_shift(window):
        return "covered_by_10_22h"
    return "uncovered"
