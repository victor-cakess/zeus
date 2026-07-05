"""Tests for src/shared/report.py — run-report shaping + email/digest formatting.

Two kinds of logic here:
  - Data shaping (build_run_report, skip_history, build_dbt_report) — assert exact
    structures.
  - String formatting (format_email, format_dbt_section, format_digest) — assert the
    body CONTAINS the key lines, not full-string equality. Contains-checks survive
    harmless wording/whitespace tweaks; equality checks would break on every edit and
    train you to blindly update expected strings (which defeats the test).

build_dbt_report is the interesting one: it consumes a dbtRunner result that report.py
never imports (it's duck-typed). We hand it a fake object with just the attributes it
reads — the same "fake matching an interface" technique as FakeS3, but for a plain
object instead of a module.
"""

from datetime import date
from types import SimpleNamespace

from shared import report


# --- Data shaping ------------------------------------------------------------------


def test_build_run_report_splits_ok_and_skipped():
    results = [
        {"unit": "A", "status": "ok", "rows": 10},
        {"unit": "B", "status": "skipped", "error": "API 500"},
        {"unit": "C", "status": "ok", "rows": 5},
    ]
    rep = report.build_run_report(date(2026, 7, 5), rows=15, results=results)

    assert rep["date"] == "2026-07-05"
    assert rep["rows"] == 15
    assert rep["succeeded"] == ["A", "C"]
    assert rep["skipped"] == [{"unit": "B", "reason": "API 500"}]


def test_build_run_report_skip_without_error_reads_unknown():
    # A skip with no error string still gets a readable reason (the _reason fallback).
    results = [{"unit": "B", "status": "skipped"}]
    rep = report.build_run_report(date(2026, 7, 5), rows=0, results=results)
    assert rep["skipped"] == [{"unit": "B", "reason": "unknown"}]


def test_skip_history_counts_per_unit_across_reports():
    reports = [
        {"skipped": [{"unit": "B", "reason": "x"}, {"unit": "C", "reason": "y"}]},
        {"skipped": [{"unit": "B", "reason": "z"}]},
        {"skipped": []},
    ]
    assert report.skip_history(reports) == {"B": 2, "C": 1}


# --- build_dbt_report: fake a duck-typed dbtRunner result --------------------------


def _node(resource_type, name):
    return SimpleNamespace(resource_type=resource_type, name=name)


def _result(resource_type, name, status):
    # Mirrors the shape report.build_dbt_report reads: r.node.{resource_type,name}, r.status.
    return SimpleNamespace(node=_node(resource_type, name), status=status)


def test_build_dbt_report_counts_models_and_tests():
    res = SimpleNamespace(
        success=False,
        exception=None,
        result=SimpleNamespace(results=[
            _result("model", "stg_eia", "success"),
            _result("model", "fct_grid", "success"),
            _result("test", "not_null_x", "pass"),
            _result("test", "unique_y", "fail"),
            _result("test", "rel_z", "error"),
        ]),
    )
    rep = report.build_dbt_report(date(2026, 7, 5), res)

    assert rep["status"] == "failed"          # res.success is False
    assert rep["models_built"] == 2
    assert rep["tests_passed"] == 1
    assert rep["tests_failed"] == 2
    assert sorted(rep["failed_tests"]) == ["rel_z", "unique_y"]


def test_build_dbt_report_handles_no_result():
    # dbt can crash (parse/connection) before producing any results → res.result is None.
    res = SimpleNamespace(success=False, exception=RuntimeError("bad profile"), result=None)
    rep = report.build_dbt_report(date(2026, 7, 5), res)

    assert rep["models_built"] == 0
    assert rep["tests_failed"] == 0
    assert rep["error"] == "bad profile"


# --- String formatting (contains-checks) -------------------------------------------


def _run_report(snowflake):
    return {
        "date": "2026-07-05",
        "rows": 10,
        "succeeded": ["A", "B"],
        "skipped": [{"unit": "C", "reason": "API 500"}],
        "snowflake": snowflake,
    }


def test_format_email_ok_snowflake():
    rep = _run_report({"status": "ok", "rows_loaded": 10, "error": None})
    subject, body = report.format_email(rep, history={}, source="eia", history_days=7)

    assert "EIA" in subject
    assert "2 ok / 1 skipped" in subject
    assert "Snowflake: loaded 10 rows" in body
    assert "C — API 500" in body


def test_format_email_failed_snowflake():
    rep = _run_report({"status": "error", "rows_loaded": 0, "error": "invalid date"})
    _, body = report.format_email(rep, history={}, source="eia", history_days=7)
    assert "Snowflake: FAILED — invalid date" in body


def test_format_dbt_section_none_report():
    # A None dbt report means it crashed before writing one — surfaced, not hidden.
    section = report.format_dbt_section(None)
    assert "no run report" in section


def test_format_digest_combines_sources_and_dbt():
    sections = [
        ("eia", _run_report({"status": "ok", "rows_loaded": 10, "error": None}), {}),
        ("noaa", None, {}),  # noaa wrote no report today
    ]
    dbt_report = {
        "date": "2026-07-05", "models_built": 12,
        "tests_passed": 50, "tests_failed": 0, "failed_tests": [], "error": None,
    }
    subject, body = report.format_digest("2026-07-05", sections, history_days=7, dbt_report=dbt_report)

    assert subject == "Zeus daily report — 2026-07-05"
    assert "EIA ingestion complete" in body
    assert "NOAA: no run report" in body       # the None section is surfaced
    assert "DBT build" in body
    assert "Models built: 12" in body
