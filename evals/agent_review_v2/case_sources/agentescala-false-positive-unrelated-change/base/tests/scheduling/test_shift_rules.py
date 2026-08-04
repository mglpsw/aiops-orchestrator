"""Tests for shift coverage classification (synthetic fixture)."""

from backend.scheduling.shift_rules import ShiftWindow, classify_shift


def test_24h_shift_is_classified_as_covered_by_24h() -> None:
    window = ShiftWindow(start_hour=6, end_hour=6)
    assert classify_shift(window) == "covered_by_24h"
