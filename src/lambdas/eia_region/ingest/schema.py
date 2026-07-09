from datetime import date, datetime

import pyarrow as pa

SCHEMA = pa.schema([
    pa.field("period", pa.timestamp("us")),
    pa.field("respondent", pa.string()),
    pa.field("respondent_name", pa.string()),
    pa.field("type", pa.string()),
    pa.field("type_name", pa.string()),
    pa.field("value", pa.float64()),
    pa.field("value_units", pa.string()),
    pa.field("ingestion_date", pa.date32()),
])


def normalize_row(raw: dict, ingestion_date: date) -> dict:
    raw_value = raw.get("value")
    return {
        "period": datetime.strptime(raw["period"], "%Y-%m-%dT%H"),
        "respondent": raw.get("respondent"),
        "respondent_name": raw.get("respondent-name"),
        "type": raw.get("type"),
        "type_name": raw.get("type-name"),
        "value": float(raw_value) if raw_value is not None else None,
        "value_units": raw.get("value-units"),
        "ingestion_date": ingestion_date,
    }
