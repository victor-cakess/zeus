"""Tests for src/shared/ingest.py::run_ingest — the shared daily orchestrator.

This is the highest-value target: it encodes the fault-tolerance CONTRACT that the
whole platform's trustworthiness rests on:
  - a per-unit failure or empty result is a *skip*, the run continues
  - a total outage (nothing consolidated) fails hard (ValueError)
  - a Snowflake load failure fails hard (RuntimeError)
  - in BOTH hard-failure cases the run report is written FIRST, so the digest/alert
    always has the detail

We test it with the in-memory fakes from conftest (fake_s3, fake_snowflake,
fake_externals) — no AWS, no Snowflake, no network. The source-specific pieces
(schema, normalize_row, fetch_fn, window_fn) are injected, so we hand run_ingest tiny
stand-ins instead of the real EIA client.
"""

from datetime import date

import pyarrow as pa
import pytest

from shared import ingest, paths

# Must match conftest.FIXED_TODAY (the date the fake_externals fixture patches
# today_utc to). conftest isn't importable as a module — pytest special-loads it and
# shares state via fixtures, not imports — so we restate the constant here.
FIXED_TODAY = date(2026, 7, 5)

# --- Injected source-specific stand-ins -------------------------------------------

# A minimal schema/normalize pair standing in for a real source's schema.py. Keeping it
# trivial means the test exercises run_ingest's orchestration, not a source's parsing.
SCHEMA = pa.schema([
    pa.field("unit", pa.string()),
    pa.field("value", pa.int64()),
    pa.field("ingestion_date", pa.date32()),
])


def normalize_row(raw: dict, ingestion_date: date) -> dict:
    return {"unit": raw["unit"], "value": raw["value"], "ingestion_date": ingestion_date}


def window_fn(today, days):
    # run_ingest only passes (start, end) through to fetch_fn; the values are opaque to it.
    return "2026-06-28", "2026-07-05"


def make_fetch(rows_by_unit):
    """Build a fetch_fn from a {unit: rows-or-Exception} map. An Exception value is
    raised (simulating an API error → the unit should be skipped); a list is returned."""
    def fetch_fn(unit, start, end):
        result = rows_by_unit[unit]
        if isinstance(result, Exception):
            raise result
        return result
    return fetch_fn


def run(config, units, fetch_fn):
    return ingest.run_ingest(
        config=config,
        units=units,
        window_fn=window_fn,
        fetch_fn=fetch_fn,
        schema=SCHEMA,
        normalize_row=normalize_row,
    )


# --- Convenience: the keys run_ingest writes for the fixed date --------------------

CURATED_KEY = paths.curated_prefix("eia", FIXED_TODAY) + "eia_grid.parquet"
REPORT_KEY = paths.report_key("eia", FIXED_TODAY)


# --- Tests -------------------------------------------------------------------------


def test_happy_path(ingest_config, fake_s3, fake_snowflake):
    fetch = make_fetch({
        "A": [{"unit": "A", "value": 1}],
        "B": [{"unit": "B", "value": 2}],
    })

    result = run(ingest_config, ["A", "B"], fetch)

    # Return shape (the contract the state machine reads).
    assert result["rows"] == 2
    assert result["key"] == CURATED_KEY
    assert result["succeeded"] == 2
    assert result["skipped"] == 0
    assert result["snowflake_rows_loaded"] == 42

    # The curated Parquet and the run report both landed in S3.
    assert CURATED_KEY in fake_s3.store
    report = fake_s3.store[REPORT_KEY]
    assert sorted(report["succeeded"]) == ["A", "B"]
    assert report["skipped"] == []
    assert report["snowflake"] == {"status": "ok", "rows_loaded": 42, "error": None}


def test_partial_failure_is_skipped_run_continues(ingest_config, fake_s3, fake_snowflake):
    # B's fetch raises; the run must still succeed on A alone.
    fetch = make_fetch({
        "A": [{"unit": "A", "value": 1}],
        "B": RuntimeError("API 500"),
    })

    result = run(ingest_config, ["A", "B"], fetch)

    assert result["rows"] == 1
    assert result["succeeded"] == 1
    assert result["skipped"] == 1

    report = fake_s3.store[REPORT_KEY]
    assert report["succeeded"] == ["A"]
    assert report["skipped"] == [{"unit": "B", "reason": "API 500"}]


def test_empty_unit_is_skipped(ingest_config, fake_s3, fake_snowflake):
    # An empty result (not an error) is also a skip, with a "no data" reason.
    fetch = make_fetch({
        "A": [{"unit": "A", "value": 1}],
        "B": [],
    })

    result = run(ingest_config, ["A", "B"], fetch)

    assert result["succeeded"] == 1
    assert result["skipped"] == 1
    report = fake_s3.store[REPORT_KEY]
    assert report["skipped"][0]["unit"] == "B"
    assert "no data" in report["skipped"][0]["reason"]


def test_total_outage_raises_but_writes_report_first(ingest_config, fake_s3, fake_snowflake):
    # Every unit empty → nothing consolidates. run_ingest must raise ValueError, but the
    # report must already be in S3 (so the digest/alert has the detail). This is the
    # "report before raise" contract.
    fetch = make_fetch({"A": [], "B": []})

    with pytest.raises(ValueError):
        run(ingest_config, ["A", "B"], fetch)

    assert REPORT_KEY in fake_s3.store
    report = fake_s3.store[REPORT_KEY]
    assert report["rows"] == 0
    assert len(report["skipped"]) == 2
    # No curated Parquet is written when there are no rows.
    assert CURATED_KEY not in fake_s3.store


def test_snowflake_failure_raises_but_writes_report_first(ingest_config, fake_s3, fake_snowflake):
    # Consolidation succeeds but the COPY fails. run_ingest must raise RuntimeError, with
    # the report already written (snowflake status = error) and the curated Parquet still
    # safely in S3 (it's re-runnable).
    fake_snowflake.error = RuntimeError("copy failed: invalid date")
    fetch = make_fetch({"A": [{"unit": "A", "value": 1}]})

    with pytest.raises(RuntimeError, match="snowflake load failed"):
        run(ingest_config, ["A"], fetch)

    assert CURATED_KEY in fake_s3.store  # curated data is safe
    report = fake_s3.store[REPORT_KEY]
    assert report["snowflake"]["status"] == "error"
    assert "copy failed" in report["snowflake"]["error"]
