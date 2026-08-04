"""Minimum rest period compliance rules (synthetic, AgentEscala benchmark fixture).

Computes the rest period between the end of one shift and the start of
the next, and checks it against the minimum required rest window. An
overnight gap (next shift starts on the following day) must be wrapped
through midnight; a same-day gap must never be wrapped -- wrapping a
same-day gap would silently inflate a real, short rest period into an
apparently-compliant one.
"""

from __future__ import annotations

MINIMUM_REST_HOURS = 11


def rest_hours_between(previous_shift_end_hour: int, next_shift_start_hour: int) -> int:
    """Hours of rest between the end of one shift and the start of the
    next. Only a genuinely overnight gap (next_shift_start_hour before
    previous_shift_end_hour) wraps through midnight; a same-day gap is
    returned as-is."""
    gap = next_shift_start_hour - previous_shift_end_hour
    if gap < 0:
        gap += 24
    return gap


def is_rest_period_compliant(previous_shift_end_hour: int, next_shift_start_hour: int) -> bool:
    """A rest period is compliant only when it meets or exceeds the
    minimum required rest window."""
    return rest_hours_between(previous_shift_end_hour, next_shift_start_hour) >= MINIMUM_REST_HOURS
