import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
from client import fetch_unit
from schema import SCHEMA, normalize_row
from shared import paths, report, s3_io, snowflake_io, ssm, time_window

BUCKET = os.environ["BUCKET"]
SOURCE = os.environ["SOURCE"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "20"))

SNOWFLAKE_ACCOUNT = os.environ["SNOWFLAKE_ACCOUNT"]
SNOWFLAKE_USER = os.environ["SNOWFLAKE_USER"]
SNOWFLAKE_ROLE = os.environ["SNOWFLAKE_ROLE"]
SNOWFLAKE_WAREHOUSE = os.environ["SNOWFLAKE_WAREHOUSE"]
SNOWFLAKE_DATABASE = os.environ["SNOWFLAKE_DATABASE"]
SNOWFLAKE_SCHEMA = os.environ["SNOWFLAKE_SCHEMA"]
SNOWFLAKE_TABLE = os.environ["SNOWFLAKE_TABLE"]
SNOWFLAKE_STAGE = os.environ["SNOWFLAKE_STAGE"]
SNOWFLAKE_KEY_SSM_PATH = os.environ["SNOWFLAKE_PRIVATE_KEY_SSM_PATH"]


def _load_snowflake(today: date) -> int:
    """COPY the day's curated Parquet into Snowflake. Snowflake reads the file from
    S3 via the stage's storage integration; we only submit the statement. Targets
    just today's partition — the stage's load metadata makes re-runs of the same
    file a no-op. Returns rows loaded."""
    stage_root = f"curated/{SOURCE}/"
    day_path = paths.curated_prefix(SOURCE, today)[len(stage_root):]
    statement = (
        f"COPY INTO {SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}.{SNOWFLAKE_TABLE} "
        f"FROM @{SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}.{SNOWFLAKE_STAGE}/{day_path} "
        # USE_LOGICAL_TYPE = TRUE makes Snowflake honor the Parquet DATE logical
        # type; without it `date` loads as a raw INT32 day-count → wrong date.
        f"FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE) "
        f"MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE"
    )
    return snowflake_io.copy_into(
        account=SNOWFLAKE_ACCOUNT,
        user=SNOWFLAKE_USER,
        private_key_pem=ssm.get_parameter(SNOWFLAKE_KEY_SSM_PATH),
        role=SNOWFLAKE_ROLE,
        warehouse=SNOWFLAKE_WAREHOUSE,
        database=SNOWFLAKE_DATABASE,
        schema=SNOWFLAKE_SCHEMA,
        statement=statement,
    )


def _fetch_one(unit: str, start: str, end: str, today: date) -> dict:
    """Fetch one BA's stations and write its raw JSON. A failure or empty result is
    a skip, not a hard error — the run consolidates whatever landed."""
    try:
        rows = fetch_unit(unit, start, end)
        if not rows:
            return {"unit": unit, "status": "skipped", "error": f"no data ({start} to {end})"}
        s3_io.put_json(BUCKET, paths.raw_key(SOURCE, unit, today), rows)
        return {"unit": unit, "status": "ok", "rows": len(rows)}
    except Exception as e:  # noqa: BLE001 — any per-BA failure is a skip
        return {"unit": unit, "status": "skipped", "error": str(e)}


def lambda_handler(event, context) -> dict:
    today = time_window.today_utc()
    start, end = time_window.lookback_window_dates(today, LOOKBACK_DAYS)
    units = event["units"]

    # Fan out the per-BA fetch + raw write; each BA fetches its full station list.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(
            pool.map(lambda u: _fetch_one(u, start, end, today), units)
        )

    # Consolidate whatever raw files landed (partial data is fine). NOAA raw files
    # are flat JSON arrays of rows, so flatten each file here.
    prefix = paths.raw_prefix(SOURCE, today)
    rows = [
        normalize_row(r, today)
        for obj in s3_io.iter_objects(BUCKET, prefix)
        for r in obj
    ]

    out_key = paths.curated_prefix(SOURCE, today) + f"{SOURCE}_grid.parquet"
    snowflake_load = None
    if rows:
        table = pa.Table.from_pylist(rows, schema=SCHEMA)
        buf = BytesIO()
        pq.write_table(table, buf, compression="snappy")
        s3_io.put_bytes(BUCKET, out_key, buf.getvalue())

        # Load the day's Parquet into Snowflake. A failure here must not swallow the
        # run report, so capture it and re-raise after the report is written.
        try:
            snowflake_load = {"status": "ok", "rows_loaded": _load_snowflake(today), "error": None}
        except Exception as e:  # noqa: BLE001 — surfaced in the report, re-raised below
            snowflake_load = {"status": "error", "rows_loaded": 0, "error": str(e)}

    # Write this run's report — the daily digest Lambda reads it and emails the
    # combined cross-source summary. The on-failure destination covers hard crashes.
    run_report = report.build_run_report(today, len(rows), results)
    run_report["snowflake"] = snowflake_load
    s3_io.put_json(BUCKET, paths.report_key(SOURCE, today), run_report)

    # Total outage: nothing consolidated — fail hard so the invocation errors and
    # the Lambda on-failure destination alerts.
    if not rows:
        raise ValueError(f"no rows under {prefix}")

    # Consolidation succeeded but the Snowflake load failed: the curated Parquet is
    # safe in S3 (re-runnable; load metadata skips already-loaded files). Raise so
    # the on-failure destination alerts.
    if snowflake_load["status"] == "error":
        raise RuntimeError(f"snowflake load failed: {snowflake_load['error']}")

    return {
        "rows": len(rows),
        "key": out_key,
        "succeeded": len(run_report["succeeded"]),
        "skipped": len(run_report["skipped"]),
        "snowflake_rows_loaded": snowflake_load["rows_loaded"],
    }
