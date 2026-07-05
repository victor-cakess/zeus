"""Tests for src/shared/time_window.py — the two lookback-window builders.

Pure date arithmetic, like paths.py: no I/O. The two functions differ only in output
shape — hourly ('...T00'/'...T23') for EIA, plain dates for the daily NOAA/FRED APIs.
today_utc() itself just wraps datetime.now, so there's nothing worth testing there (a
test would only re-assert the stdlib); we test the two windowing functions instead.
"""

from datetime import date

import pytest

from shared import time_window


def test_lookback_window_hourly_form():
    # 7-day window ending today: start at hour 00, end at hour 23, so the day is fully
    # covered on both ends.
    start, end = time_window.lookback_window(date(2026, 7, 5), 7)
    assert start == "2026-06-28T00"
    assert end == "2026-07-05T23"


def test_lookback_window_dates_plain_form():
    # Same span, but plain YYYY-MM-DD for the date-grain APIs.
    start, end = time_window.lookback_window_dates(date(2026, 7, 5), 7)
    assert start == "2026-06-28"
    assert end == "2026-07-05"


@pytest.mark.parametrize(
    "today, days, expected_start",
    [
        (date(2026, 7, 5), 7, "2026-06-28"),
        (date(2026, 1, 3), 7, "2025-12-27"),    # crosses the year boundary
        (date(2026, 3, 1), 1, "2026-02-28"),    # crosses month boundary (non-leap Feb)
        (date(2026, 7, 5), 150, "2026-02-05"),  # FRED's wide 150-day window
    ],
)
def test_lookback_window_dates_boundaries(today, days, expected_start):
    start, end = time_window.lookback_window_dates(today, days)
    assert start == expected_start
    assert end == today.isoformat()
