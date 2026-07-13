"""Paginated fetch loop for the EIA v2 electricity/rto routes.

All EIA-family sources (eia = fuel-type-data, eia_region = region-data,
eia_interchange = interchange-data) hit the same API shape: hourly frequency,
one `value` data column, one facet naming the fan-out unit, offset pagination.
Only the route URL and the facet name differ, so each source's client.py binds
those two and delegates here (rule of three — see the ADR entry).
"""

import time

import requests

PAGE_SIZE = 5000


def fetch_page(
    base_url: str,
    facet: str,
    unit: str,
    start: str,
    end: str,
    offset: int,
    api_key: str,
    retries: int = 3,
) -> dict:
    params = {
        "api_key": api_key,
        "frequency": "hourly",
        "data[0]": "value",
        f"facets[{facet}][]": unit,
        "start": start,
        "end": end,
        "length": PAGE_SIZE,
        "offset": offset,
    }
    for attempt in range(retries):
        response = requests.get(base_url, params=params, timeout=60)
        if response.status_code in (502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()["response"]
    raise RuntimeError(f"failed after {retries} retries: {unit} offset={offset}")


def fetch_unit(base_url: str, facet: str, unit: str, start: str, end: str, api_key: str) -> list[dict]:
    rows: list[dict] = []
    first = fetch_page(base_url, facet, unit, start, end, 0, api_key)
    total = int(first["total"])
    rows.extend(first["data"])

    offset = PAGE_SIZE
    while offset < total:
        page = fetch_page(base_url, facet, unit, start, end, offset, api_key)
        rows.extend(page["data"])
        offset += PAGE_SIZE
        time.sleep(0.2)
    return rows
