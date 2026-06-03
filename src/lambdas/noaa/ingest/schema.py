from datetime import date, datetime

import pyarrow as pa

# Wide daily schema: one row per (ba, station, date). Field names are lower-case so
# the Snowflake COPY's MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE maps them onto the
# upper-case NOAA_GRID columns. The dataTypes mirror client.DATA_TYPES; NCEI omits
# any a station didn't report, so they land null.
_DATA_TYPES = [
    "TMAX", "TMIN", "TAVG",
    "PRCP", "SNOW", "SNWD",
    "AWND", "WSF2", "WSF5", "WDF2",
    "RHAV", "ASLP", "ADPT",
]

SCHEMA = pa.schema(
    [
        pa.field("date", pa.date32()),
        pa.field("station", pa.string()),
        pa.field("ba", pa.string()),
    ]
    + [pa.field(dt.lower(), pa.float64()) for dt in _DATA_TYPES]
    + [pa.field("ingestion_date", pa.date32())]
)


def _to_float(v):
    return float(v) if v not in (None, "") else None


def normalize_row(raw: dict, ingestion_date: date) -> dict:
    row = {
        "date": datetime.strptime(raw["DATE"], "%Y-%m-%d").date(),
        "station": raw.get("STATION"),
        "ba": raw.get("ba"),
        "ingestion_date": ingestion_date,
    }
    for dt in _DATA_TYPES:
        row[dt.lower()] = _to_float(raw.get(dt))
    return row
