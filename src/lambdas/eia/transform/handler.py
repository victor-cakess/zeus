import json
import os
from datetime import datetime, timezone
from io import BytesIO

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

BUCKET = os.environ["BUCKET"]

s3 = boto3.client("s3")

SCHEMA = pa.schema([
    pa.field("period", pa.timestamp("us")),
    pa.field("respondent", pa.string()),
    pa.field("respondent_name", pa.string()),
    pa.field("fueltype", pa.string()),
    pa.field("type_name", pa.string()),
    pa.field("value", pa.float64()),
    pa.field("value_units", pa.string()),
    pa.field("ingestion_date", pa.date32()),
])


def lambda_handler(event, context):
    today = datetime.now(timezone.utc).date()
    raw_prefix = (
        f"raw/eia/"
        f"ingestion_year={today.year:04d}/"
        f"ingestion_month={today.month:02d}/"
        f"ingestion_day={today.day:02d}/"
    )

    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=raw_prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))

    if not keys:
        raise ValueError(f"no raw files found under {raw_prefix}")

    rows = []
    for key in keys:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        data = json.loads(obj["Body"].read())
        for row in data:
            raw_value = row.get("value")
            rows.append({
                "period": datetime.strptime(row["period"], "%Y-%m-%dT%H"),
                "respondent": row.get("respondent"),
                "respondent_name": row.get("respondent-name"),
                "fueltype": row.get("fueltype"),
                "type_name": row.get("type-name"),
                "value": float(raw_value) if raw_value is not None else None,
                "value_units": row.get("value-units"),
                "ingestion_date": today,
            })

    if not rows:
        raise ValueError("all raw files were empty after parsing")

    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    buf = BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)

    out_key = (
        f"curated/eia/"
        f"ingestion_year={today.year:04d}/"
        f"ingestion_month={today.month:02d}/"
        f"ingestion_day={today.day:02d}/"
        "eia_grid.parquet"
    )
    s3.put_object(Bucket=BUCKET, Key=out_key, Body=buf.getvalue())

    return {"rows": len(rows), "files": len(keys), "key": out_key}
