import os
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
from schema import SCHEMA, normalize_row
from shared import paths, s3_io, time_window

BUCKET = os.environ["BUCKET"]
SOURCE = os.environ["SOURCE"]


def lambda_handler(event, context) -> dict:
    today = time_window.today_utc()
    prefix = paths.raw_prefix(SOURCE, today)

    rows = [normalize_row(r, today) for r in s3_io.iter_json_objects(BUCKET, prefix)]
    if not rows:
        raise ValueError(f"no rows under {prefix}")

    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    buf = BytesIO()
    pq.write_table(table, buf, compression="snappy")

    out_key = paths.curated_prefix(SOURCE, today) + f"{SOURCE}_grid.parquet"
    s3_io.put_bytes(BUCKET, out_key, buf.getvalue())

    return {"rows": len(rows), "key": out_key}
