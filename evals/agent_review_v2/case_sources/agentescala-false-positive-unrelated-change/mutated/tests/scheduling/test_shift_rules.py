"""Tests for shift coverage classification (synthetic fixture)."""

from backend.scheduling.shift_rules import ShiftWindow, classify_shift


def test_24h_shift_is_classified_as_covered_by_24h() -> None:
    # regression guard: any wrap-around start==end hour must resolve to 24H
    window = ShiftWindow(start_hour=6, end_hour=6)
    assert classify_shift(window) == "covered_by_24h"


def test_uncovered_shift_returns_uncovered() -> None:
    window = ShiftWindow(start_hour=3, end_hour=5)
    assert classify_shift(window) == "uncovered"
