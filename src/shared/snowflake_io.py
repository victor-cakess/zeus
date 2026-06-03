"""Thin Snowflake helper: open a key-pair connection, run one statement, return
rows loaded. Source-agnostic — the caller builds the SQL (a daily one-partition
COPY in the Lambda, a whole-stage COPY in the backfill), so the same `copy_into`
serves both."""

import snowflake.connector
from cryptography.hazmat.primitives import serialization


def copy_statement(database: str, schema: str, table: str, stage: str, path: str = "") -> str:
    """Build a `COPY INTO <table> FROM @<stage>/<path>` for a Parquet stage. The daily
    handlers pass the day-partition `path`; a whole-stage load passes `""`.
    USE_LOGICAL_TYPE = TRUE makes Snowflake honor Parquet DATE/TIMESTAMP logical types
    (without it EIA's `period` loads as INT64 and NOAA's `date` as INT32 → "Invalid date");
    MATCH_BY_COLUMN_NAME maps Parquet fields onto table columns by name."""
    return (
        f"COPY INTO {database}.{schema}.{table} "
        f"FROM @{database}.{schema}.{stage}/{path} "
        f"FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE) "
        f"MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE"
    )


def _der_from_pem(pem: str) -> bytes:
    """PKCS8 PEM private key (as stored in SSM) → DER bytes for the connector."""
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _rows_loaded(cursor) -> int:
    """Sum the rows_loaded column of a COPY INTO result (one row per file)."""
    cols = [c[0].lower() for c in cursor.description]
    if "rows_loaded" not in cols:
        return 0
    idx = cols.index("rows_loaded")
    return sum(int(row[idx] or 0) for row in cursor.fetchall())


def copy_into(
    *,
    account: str,
    user: str,
    private_key_pem: str,
    role: str,
    warehouse: str,
    database: str,
    schema: str,
    statement: str,
) -> int:
    """Run `statement` (a COPY INTO) on a fresh key-pair connection. Returns the
    total rows loaded. Raises on any connection or SQL error."""
    conn = snowflake.connector.connect(
        account=account,
        user=user,
        private_key=_der_from_pem(private_key_pem),
        role=role,
        warehouse=warehouse,
        database=database,
        schema=schema,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(statement)
        return _rows_loaded(cursor)
    finally:
        conn.close()
