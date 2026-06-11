from datetime import date, datetime

import pyarrow as pa

# Narrow schema: one row per (series, date) — a single observed value per price
# series per observation date. Field names are lower-case so the Snowflake COPY's
# MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE maps them onto the upper-case FRED_GRID
# columns. infra/core/snowflake_fred.tf mirrors this — keep in sync.
SCHEMA = pa.schema(
    [
        pa.field("series", pa.string()),
        pa.field("series_id", pa.string()),
        pa.field("date", pa.date32()),
        pa.field("value", pa.float64()),
        pa.field("ingestion_date", pa.date32()),
    ]
)


def normalize_row(raw: dict, ingestion_date: date) -> dict:
    # Missing observations ("." values) are dropped in client.fetch_unit, so value
    # always parses here.
    return {
        "series": raw["series"],
        "series_id": raw["series_id"],
        "date": datetime.strptime(raw["date"], "%Y-%m-%d").date(),
        "value": float(raw["value"]),
        "ingestion_date": ingestion_date,
    }
