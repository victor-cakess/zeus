import json
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
import requests

BASE_URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
PAGE_SIZE = 5000

BUCKET = os.environ["BUCKET"]
EIA_API_KEY_SSM_PATH = os.environ["EIA_API_KEY_SSM_PATH"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))

s3 = boto3.client("s3")
ssm = boto3.client("ssm")

_api_key: str | None = None


def get_api_key() -> str:
    global _api_key
    if _api_key is None:
        resp = ssm.get_parameter(Name=EIA_API_KEY_SSM_PATH, WithDecryption=True)
        _api_key = resp["Parameter"]["Value"]
    return _api_key


def fetch_page(ba: str, start: str, end: str, offset: int, retries: int = 3) -> dict:
    params = {
        "api_key": get_api_key(),
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": ba,
        "start": start,
        "end": end,
        "length": PAGE_SIZE,
        "offset": offset,
    }
    for attempt in range(retries):
        response = requests.get(BASE_URL, params=params, timeout=60)
        if response.status_code in (502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()["response"]
    raise RuntimeError(f"failed after {retries} retries: {ba} offset={offset}")


def fetch_ba(ba: str, start: str, end: str) -> list[dict]:
    rows: list[dict] = []
    first = fetch_page(ba, start, end, 0)
    total = int(first["total"])
    rows.extend(first["data"])

    offset = PAGE_SIZE
    while offset < total:
        page = fetch_page(ba, start, end, offset)
        rows.extend(page["data"])
        offset += PAGE_SIZE
        time.sleep(0.2)
    return rows


def lambda_handler(event: dict, context) -> dict:
    ba = event["respondent"]

    today = datetime.now(timezone.utc).date()
    start_dt = today - timedelta(days=LOOKBACK_DAYS)
    start = f"{start_dt.isoformat()}T00"
    end = f"{today.isoformat()}T23"

    rows = fetch_ba(ba, start, end)

    key = (
        "raw/eia/"
        f"ingestion_year={today.year:04d}/"
        f"ingestion_month={today.month:02d}/"
        f"ingestion_day={today.day:02d}/"
        f"{ba}.json"
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(rows).encode("utf-8"),
        ContentType="application/json",
    )

    return {"respondent": ba, "rows": len(rows), "key": key}
