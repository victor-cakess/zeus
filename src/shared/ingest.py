"""Shared daily-ingest orchestrator. Both source Lambdas are thin shims over
`run_ingest`, injecting the two things that genuinely differ per source: the fetch
function (one source needs an api key) and the lookback window (hourly vs daily).
The orchestration — fan out the per-unit fetch → consolidate the day's raw partition
into one curated Parquet → COPY INTO Snowflake → write the run report → fail hard on
a total outage or a load error — is identical and lives here once."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Callable

import pyarrow as pa
import pyarrow.parquet as pq

from shared import paths, report, s3_io, snowflake_io, ssm, time_window


@dataclass(frozen=True)
class SnowflakeConfig:
    account: str
    user: str
    role: str
    warehouse: str
    database: str
    schema: str
    table: str
    stage: str
    key_ssm_path: str


@dataclass(frozen=True)
class IngestConfig:
    source: str
    bucket: str
    lookback_days: int
    max_workers: int
    snowflake: SnowflakeConfig


def config_from_env() -> IngestConfig:
    """Build the config from the Lambda's environment (the same vars the handlers
    read before). Isolated here so `run_ingest` takes a plain object and stays
    testable without a live environment."""
    return IngestConfig(
        source=os.environ["SOURCE"],
        bucket=os.environ["BUCKET"],
        lookback_days=int(os.environ.get("LOOKBACK_DAYS", "7")),
        max_workers=int(os.environ.get("MAX_WORKERS", "20")),
        snowflake=SnowflakeConfig(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            role=os.environ["SNOWFLAKE_ROLE"],
            warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
            database=os.environ["SNOWFLAKE_DATABASE"],
            schema=os.environ["SNOWFLAKE_SCHEMA"],
            table=os.environ["SNOWFLAKE_TABLE"],
            stage=os.environ["SNOWFLAKE_STAGE"],
            key_ssm_path=os.environ["SNOWFLAKE_PRIVATE_KEY_SSM_PATH"],
        ),
    )


def _load_snowflake(config: IngestConfig, today: date) -> int:
    """COPY the day's curated Parquet into Snowflake. Snowflake reads the file from
    S3 via the stage's storage integration; we only submit the statement. Targets
    just today's partition — the stage's load metadata makes a re-run of the same
    file a no-op. Returns rows loaded."""
    sf = config.snowflake
    day_path = paths.curated_prefix(config.source, today)[len(f"curated/{config.source}/"):]
    statement = snowflake_io.copy_statement(sf.database, sf.schema, sf.table, sf.stage, day_path)
    return snowflake_io.copy_into(
        account=sf.account,
        user=sf.user,
        private_key_pem=ssm.get_parameter(sf.key_ssm_path),
        role=sf.role,
        warehouse=sf.warehouse,
        database=sf.database,
        schema=sf.schema,
        statement=statement,
    )


def _fetch_one(config, fetch_fn, unit, start, end, today) -> dict:
    """Fetch one unit and write its raw JSON. A failure or empty result is a skip,
    not a hard error — the run consolidates whatever landed."""
    try:
        rows = fetch_fn(unit, start, end)
        if not rows:
            return {"unit": unit, "status": "skipped", "error": f"no data ({start} to {end})"}
        s3_io.put_json(config.bucket, paths.raw_key(config.source, unit, today), rows)
        return {"unit": unit, "status": "ok", "rows": len(rows)}
    except Exception as e:  # noqa: BLE001 — any per-unit failure is a skip
        return {"unit": unit, "status": "skipped", "error": str(e)}


def run_ingest(
    *,
    config: IngestConfig,
    units: list,
    window_fn: Callable,
    fetch_fn: Callable,
    schema,
    normalize_row: Callable,
) -> dict:
    """One daily run. The source-specific pieces (`fetch_fn`, `window_fn`, `schema`,
    `normalize_row`) are injected; everything else is identical across sources."""
    today = time_window.today_utc()
    start, end = window_fn(today, config.lookback_days)

    # Fan out the per-unit fetch + raw write across the thread pool.
    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        results = list(
            pool.map(lambda u: _fetch_one(config, fetch_fn, u, start, end, today), units)
        )

    # Consolidate whatever raw files landed (partial data is fine). Raw files are
    # flat JSON arrays of rows, so flatten each file here.
    prefix = paths.raw_prefix(config.source, today)
    rows = [
        normalize_row(r, today)
        for obj in s3_io.iter_objects(config.bucket, prefix)
        for r in obj
    ]

    out_key = paths.curated_prefix(config.source, today) + f"{config.source}_grid.parquet"
    snowflake_load = None
    if rows:
        table = pa.Table.from_pylist(rows, schema=schema)
        buf = BytesIO()
        pq.write_table(table, buf, compression="snappy")
        s3_io.put_bytes(config.bucket, out_key, buf.getvalue())

        # Load the day's Parquet into Snowflake. A failure here must not swallow the
        # run report, so capture it and re-raise after the report is written.
        try:
            snowflake_load = {"status": "ok", "rows_loaded": _load_snowflake(config, today), "error": None}
        except Exception as e:  # noqa: BLE001 — surfaced in the report, re-raised below
            snowflake_load = {"status": "error", "rows_loaded": 0, "error": str(e)}

    # Write this run's report — the daily digest Lambda reads it and emails the
    # combined cross-source summary. The on-failure destination covers hard crashes.
    run_report = report.build_run_report(today, len(rows), results)
    run_report["snowflake"] = snowflake_load
    s3_io.put_json(config.bucket, paths.report_key(config.source, today), run_report)

    # Total outage: nothing consolidated — fail hard so the invocation errors and the
    # Lambda on-failure destination alerts.
    if not rows:
        raise ValueError(f"no rows under {prefix}")

    # Consolidation succeeded but the Snowflake load failed: the curated Parquet is
    # safe in S3 (re-runnable; load metadata skips already-loaded files). Raise so the
    # on-failure destination alerts.
    if snowflake_load["status"] == "error":
        raise RuntimeError(f"snowflake load failed: {snowflake_load['error']}")

    return {
        "rows": len(rows),
        "key": out_key,
        "succeeded": len(run_report["succeeded"]),
        "skipped": len(run_report["skipped"]),
        "snowflake_rows_loaded": snowflake_load["rows_loaded"],
    }
