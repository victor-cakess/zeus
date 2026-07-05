"""Tests for src/shared/paths.py — the single owner of the S3 key layout.

paths.py is pure string/date logic: no I/O, no mocking needed. That makes it the
ideal first module to test. Two flavours of test here:

  1. Exact-string assertions — the layout is a contract other systems depend on
     (the dbt sources, the Snowflake stage prefixes), so we pin the exact bytes.
  2. A round-trip property test — partition_date() is the declared inverse of the
     ingestion_year=/month=/day= partition that raw_key() builds, so we assert
     `partition_date(raw_key(...)) == the date we put in` across many dates. A
     round-trip test is strong: it catches drift on either side without us having to
     hand-write the expected string for every date.
"""

from datetime import date

import pytest

from shared import paths


def test_raw_key_format():
    # A raw file is <unit>.json under the date partition. Exact string — this is the
    # key an ingest Lambda writes and the layout consumers read.
    key = paths.raw_key("eia", "PJM", date(2026, 7, 5))
    assert key == "raw/eia/ingestion_year=2026/ingestion_month=07/ingestion_day=05/PJM.json"


def test_curated_prefix_format():
    prefix = paths.curated_prefix("noaa", date(2026, 7, 5))
    assert prefix == "curated/noaa/ingestion_year=2026/ingestion_month=07/ingestion_day=05/"


def test_report_key_format():
    key = paths.report_key("fred", date(2026, 7, 5))
    assert key == "reports/fred/ingestion_year=2026/ingestion_month=07/ingestion_day=05/run_report.json"


def test_partition_zero_pads_single_digits():
    # Jan 9th → month=01, day=09 (not month=1, day=9). Zero-padding matters: Hive-style
    # partitions sort lexically, and a consumer globbing month=07 must not match month=7.
    prefix = paths.curated_prefix("eia", date(2026, 1, 9))
    assert "ingestion_month=01/" in prefix
    assert "ingestion_day=09/" in prefix


# parametrize runs this test once per tuple below, each as its own reported case. The
# list mixes single- and double-digit months/days and a leap day to exercise the padding
# and the date math at boundaries.
@pytest.mark.parametrize(
    "d",
    [
        date(2026, 7, 5),
        date(2026, 1, 9),      # single-digit month and day
        date(2024, 2, 29),     # leap day
        date(2026, 12, 31),    # year/month upper boundary
    ],
)
def test_partition_date_is_inverse_of_raw_key(d):
    # The core property: build a key from a date, parse the date back out, get the same
    # date. If either raw_key's partition layout or partition_date's parser changes
    # without the other, this fails.
    key = paths.raw_key("eia", "PJM", d)
    assert paths.partition_date(key) == d
