import os
from datetime import date, timedelta

from shared import paths, report, s3_io, sns, time_window

BUCKET = os.environ["BUCKET"]
SOURCES = [s.strip() for s in os.environ["SOURCES"].split(",") if s.strip()]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
SKIP_HISTORY_DAYS = int(os.environ.get("SKIP_HISTORY_DAYS", "30"))


def _skip_history(source: str, today: date) -> dict[str, int]:
    cutoff = today - timedelta(days=SKIP_HISTORY_DAYS - 1)
    root = f"reports/{source}/"
    keys = [
        k for k in s3_io.list_keys(BUCKET, root) if paths.partition_date(k) >= cutoff
    ]
    reports = [s3_io.get_json(BUCKET, k) for k in keys]
    return report.skip_history(reports)


def _todays_report(source: str, today: date):
    """Today's run report for a source, or None if the pipeline wrote none (it
    crashed — its own on-failure alert already fired; the digest just notes it)."""
    try:
        return s3_io.get_json(BUCKET, paths.report_key(source, today))
    except Exception:  # noqa: BLE001 — a missing report is surfaced, not fatal
        return None


def lambda_handler(event, context) -> dict:
    today = time_window.today_utc()
    sections = [
        (source, _todays_report(source, today), _skip_history(source, today))
        for source in SOURCES
    ]
    subject, body = report.format_digest(today.isoformat(), sections, SKIP_HISTORY_DAYS)
    sns.publish(SNS_TOPIC_ARN, subject, body)
    return {
        "date": today.isoformat(),
        "sources": SOURCES,
        "reported": [source for source, run_report, _ in sections if run_report is not None],
    }
