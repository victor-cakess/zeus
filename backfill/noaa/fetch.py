"""429/transient-aware retry wrapper around the production NOAA client.

client.fetch_unit retries 502/503/504 per station internally, but a 429 (rate
limit) or a transient connection error bubbles up. Under local concurrency a burst
is possible, so wrap the whole fetch_unit call: on 429 or a transient error, back
off (honoring Retry-After when present) and retry. A retry refetches the BA's
stations — acceptable for a one-time job. The shared Lambda client.py is untouched.
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


def fetch_with_retry(unit: str, start: str, end: str) -> list[dict]:
    """Retry wrapper around client.fetch_unit (one batched NCEI request per BA)."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.fetch_unit(unit, start, end)
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
