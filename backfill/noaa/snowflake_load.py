"""Phase C: load every curated NOAA Parquet in S3 into ZEUS_DEV.NOAA.NOAA_GRID with
a single whole-stage COPY INTO. Run after extract + transform have landed
curated/noaa/.

    SNOWFLAKE_ACCOUNT=<org-account> SNOWFLAKE_PRIVATE_KEY_FILE=sf_noaa_loader.p8 \\
        uv run python snowflake_load.py

Same operation as the daily Lambda load with the day-path removed: the stage covers
the whole curated/noaa/ prefix, and Snowflake's per-file load metadata loads each
Parquet exactly once. Reuses the production COPY helper (src/shared/snowflake_io.py).

Run ONCE. Do not re-run months later — load metadata expires after 64 days, so a
second whole-stage COPY would re-load files older than that as duplicate loads.

Config via env: SNOWFLAKE_ACCOUNT (required), SNOWFLAKE_PRIVATE_KEY_FILE (required,
path to the loader .p8). The rest default to the ZEUS_DEV/NOAA names.
"""

import os
import sys

import _bootstrap  # noqa: F401  (sets sys.path)
from shared import snowflake_io


def main():
    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    key_file = os.environ.get("SNOWFLAKE_PRIVATE_KEY_FILE")
    if not account or not key_file:
        sys.exit("SNOWFLAKE_ACCOUNT and SNOWFLAKE_PRIVATE_KEY_FILE are required")

    database = os.environ.get("SNOWFLAKE_DATABASE", "ZEUS_DEV")
    schema = os.environ.get("SNOWFLAKE_SCHEMA", "NOAA")
    table = os.environ.get("SNOWFLAKE_TABLE", "NOAA_GRID")
    stage = os.environ.get("SNOWFLAKE_STAGE", "NOAA_STAGE")

    with open(key_file) as f:
        private_key_pem = f.read()

    statement = (
        f"COPY INTO {database}.{schema}.{table} "
        f"FROM @{database}.{schema}.{stage}/ "
        # USE_LOGICAL_TYPE = TRUE: honor the Parquet DATE logical type; without it
        # `date` loads as a raw INT32 day-count → wrong date.
        f"FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE) "
        f"MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE"
    )

    print(f"backfill COPY INTO {database}.{schema}.{table} (whole stage)…", flush=True)
    rows = snowflake_io.copy_into(
        account=account,
        user=os.environ.get("SNOWFLAKE_USER", "ZEUS_DEV_NOAA_LOADER"),
        private_key_pem=private_key_pem,
        role=os.environ.get("SNOWFLAKE_ROLE", "ZEUS_DEV_NOAA_LOADER_ROLE"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "ZEUS_DEV_WH"),
        database=database,
        schema=schema,
        statement=statement,
    )
    print(f"done — {rows} rows loaded", flush=True)


if __name__ == "__main__":
    main()
