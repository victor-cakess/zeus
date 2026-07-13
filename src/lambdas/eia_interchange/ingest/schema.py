from datetime import date, datetime

import pyarrow as pa

SCHEMA = pa.schema([
    pa.field("period", pa.timestamp("us")),
    pa.field("fromba", pa.string()),
    pa.field("fromba_name", pa.string()),
    pa.field("toba", pa.string()),
    pa.field("toba_name", pa.string()),
    pa.field("value", pa.float64()),
    pa.field("value_units", pa.string()),
    pa.field("ingestion_date", pa.date32()),
])


def normalize_row(raw: dict, ingestion_date: date) -> dict:
    raw_value = raw.get("value")
    return {
        "period": datetime.strptime(raw["period"], "%Y-%m-%dT%H"),
        "fromba": raw.get("fromba"),
        "fromba_name": raw.get("fromba-name"),
        "toba": raw.get("toba"),
        "toba_name": raw.get("toba-name"),
        "value": float(raw_value) if raw_value is not None else None,
        "value_units": raw.get("value-units"),
        "ingestion_date": ingestion_date,
    }
