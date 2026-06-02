import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
import report
from client import fetch_unit
from schema import SCHEMA, normalize_row
from shared import paths, s3_io, sns, ssm, time_window

BUCKET = os.environ["BUCKET"]
SOURCE = os.environ["SOURCE"]
API_KEY_SSM_PATH = os.environ["API_KEY_SSM_PATH"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
SKIP_HISTORY_DAYS = int(os.environ.get("SKIP_HISTORY_DAYS", "30"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "20"))


def _date_from_report_key(key: str) -> date:
    parts = dict(p.split("=") for p in key.split("/") if "=" in p)
    return date(
        int(parts["ingestion_year"]),
        int(parts["ingestion_month"]),
        int(parts["ingestion_day"]),
    )


def _load_skip_history(today: date) -> dict[str, int]:
    cutoff = today - timedelta(days=SKIP_HISTORY_DAYS - 1)
    root = f"reports/{SOURCE}/"
    keys = [
        k
        for k in s3_io.list_keys(BUCKET, root)
        if _date_from_report_key(k) >= cutoff
    ]
    reports = [s3_io.get_json(BUCKET, k) for k in keys]
    return report.skip_history(reports)


def _fetch_one(unit: str, start: str, end: str, api_key: str, today: date) -> dict:
    """Fetch one BA and write its raw JSON. A failure or empty result is a
    skip, not a hard error — the run consolidates whatever landed."""
    try:
        rows = fetch_unit(unit, start, end, api_key)
        if not rows:
            return {"unit": unit, "status": "skipped", "error": f"no data ({start} to {end})"}
        s3_io.put_json(BUCKET, paths.raw_key(SOURCE, unit, today), rows)
        return {"unit": unit, "status": "ok", "rows": len(rows)}
    except Exception as e:  # noqa: BLE001 — any per-BA failure is a skip
        return {"unit": unit, "status": "skipped", "error": str(e)}


def lambda_handler(event, context) -> dict:
    today = time_window.today_utc()
    start, end = time_window.lookback_window(today, LOOKBACK_DAYS)
    units = event["units"]

    # Fetch the API key once — ssm.get_parameter caches, so resolve it before
    # the pool to avoid a race on the module-level cache across workers.
    api_key = ssm.get_parameter(API_KEY_SSM_PATH)

    # Fan out the per-BA fetch + raw write. Replaces the Step Function Map.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(
            pool.map(lambda u: _fetch_one(u, start, end, api_key, today), units)
        )

    # Consolidate whatever raw files landed (partial data is fine). EIA raw
    # files are flat JSON arrays of rows, so flatten each file here.
    prefix = paths.raw_prefix(SOURCE, today)
    rows = [
        normalize_row(r, today)
        for obj in s3_io.iter_objects(BUCKET, prefix)
        for r in obj
    ]

    out_key = paths.curated_prefix(SOURCE, today) + f"{SOURCE}_grid.parquet"
    if rows:
        table = pa.Table.from_pylist(rows, schema=SCHEMA)
        buf = BytesIO()
        pq.write_table(table, buf, compression="snappy")
        s3_io.put_bytes(BUCKET, out_key, buf.getvalue())

    # Write this run's report, then email the summary — the final step of the
    # run, before any hard-fail below.
    run_report = report.build_run_report(today, len(rows), results)
    s3_io.put_json(BUCKET, paths.report_key(SOURCE, today), run_report)

    history = _load_skip_history(today)
    subject, body = report.format_email(run_report, history, SOURCE, SKIP_HISTORY_DAYS)
    sns.publish(SNS_TOPIC_ARN, subject, body)

    # Total outage: nothing consolidated — fail hard so the invocation errors
    # and the Lambda on-failure destination alerts.
    if not rows:
        raise ValueError(f"no rows under {prefix}")

    return {
        "rows": len(rows),
        "key": out_key,
        "succeeded": len(run_report["succeeded"]),
        "skipped": len(run_report["skipped"]),
    }
