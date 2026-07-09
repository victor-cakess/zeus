"""429-aware retry wrapper around the production EIA client.

client.fetch_unit raises requests.HTTPError on 429 (it only swallows 502/503/504).
Under high local concurrency a burst of 429s is possible, so wrap the whole
fetch_unit call: on 429 or a transient connection error, back off (honoring
Retry-After when present) and retry. A retry restarts pagination for that unit —
acceptable for a one-time job. The shared Lambda client.py is left untouched.
"""

import time

import _bootstrap  # noqa: F401  (sets sys.path)
import client
import requests

MAX_ATTEMPTS = 5
BASE_BACKOFF = 5  # seconds; doubles each attempt


def _retry_after(exc: requests.HTTPError) -> float | None:
    if exc.response is None:
        return None
    value = exc.response.headers.get("Retry-After")
    return float(value) if value and value.isdigit() else None


def fetch_with_retry(unit: str, start: str, end: str, api_key: str) -> list[dict]:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.fetch_unit(unit, start, end, api_key)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status != 429 or attempt == MAX_ATTEMPTS - 1:
                raise
            wait = _retry_after(exc) or BASE_BACKOFF * (2 ** attempt)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == MAX_ATTEMPTS - 1:
                raise
            wait = BASE_BACKOFF * (2 ** attempt)
        time.sleep(wait)
    raise RuntimeError(f"unreachable: exhausted retries for {unit}")
