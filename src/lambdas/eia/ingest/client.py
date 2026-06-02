import time

import requests

BASE_URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
PAGE_SIZE = 5000


def fetch_page(unit: str, start: str, end: str, offset: int, api_key: str, retries: int = 3) -> dict:
    params = {
        "api_key": api_key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": unit,
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
    raise RuntimeError(f"failed after {retries} retries: {unit} offset={offset}")


def fetch_unit(unit: str, start: str, end: str, api_key: str) -> list[dict]:
    rows: list[dict] = []
    first = fetch_page(unit, start, end, 0, api_key)
    total = int(first["total"])
    rows.extend(first["data"])

    offset = PAGE_SIZE
    while offset < total:
        page = fetch_page(unit, start, end, offset, api_key)
        rows.extend(page["data"])
        offset += PAGE_SIZE
        time.sleep(0.2)
    return rows
