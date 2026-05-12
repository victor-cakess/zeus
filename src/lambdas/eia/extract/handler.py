import os

from client import fetch_unit
from shared import paths, s3_io, ssm, time_window

BUCKET = os.environ["BUCKET"]
API_KEY_SSM_PATH = os.environ["API_KEY_SSM_PATH"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
SOURCE = os.environ["SOURCE"]


def lambda_handler(event: dict, context) -> dict:
    unit = event["unit"]
    today = time_window.today_utc()
    start, end = time_window.lookback_window(today, LOOKBACK_DAYS)

    rows = fetch_unit(unit, start, end, ssm.get_parameter(API_KEY_SSM_PATH))
    if not rows:
        raise ValueError(f"no data for unit '{unit}' ({start} to {end})")

    key = paths.raw_key(SOURCE, unit, today)
    s3_io.put_json(BUCKET, key, rows)

    return {"unit": unit, "rows": len(rows), "key": key}
