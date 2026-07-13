"""Tests for shared/eia_api.py — the paginated fetch loop all three EIA clients bind.

No network: `requests` is swapped for a fake on the eia_api module object (the
monkeypatching-module-attributes convention from CLAUDE.md), and the backoff sleep
is neutralised. What's under test: the offset pagination against the API's `total`,
the facet/route parameterization, and the 5xx retry/raise behaviour.
"""

import pytest
import requests as real_requests

from shared import eia_api

BASE_URL = "https://api.eia.gov/v2/electricity/rto/interchange-data/data/"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise real_requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class FakeRequests:
    """Returns queued responses in order and records every call's params."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return self.responses.pop(0)


def _page(rows, total):
    return FakeResponse(payload={"response": {"total": total, "data": rows}})


def test_fetch_unit_paginates_to_total(monkeypatch):
    # total = 2.4 pages → expect exactly three requests at offsets 0, 5000, 10000.
    total = 12000
    fake = FakeRequests([
        _page([{"n": i} for i in range(5000)], total),
        _page([{"n": i} for i in range(5000)], total),
        _page([{"n": i} for i in range(2000)], total),
    ])
    monkeypatch.setattr(eia_api, "requests", fake)
    monkeypatch.setattr(eia_api.time, "sleep", lambda s: None)

    rows = eia_api.fetch_unit(BASE_URL, "fromba", "PJM", "2026-01-01T00", "2026-01-07T23", "k")

    assert len(rows) == total
    assert [c["params"]["offset"] for c in fake.calls] == [0, 5000, 10000]
    # The route and facet are caller-supplied — that's the whole point of the extraction.
    assert all(c["url"] == BASE_URL for c in fake.calls)
    assert all(c["params"]["facets[fromba][]"] == "PJM" for c in fake.calls)


def test_fetch_page_retries_transient_5xx(monkeypatch):
    fake = FakeRequests([
        FakeResponse(status_code=503),
        _page([{"n": 1}], 1),
    ])
    monkeypatch.setattr(eia_api, "requests", fake)
    monkeypatch.setattr(eia_api.time, "sleep", lambda s: None)

    page = eia_api.fetch_page(BASE_URL, "respondent", "PJM", "s", "e", 0, "k")

    assert page["total"] == 1
    assert len(fake.calls) == 2  # one 503, one success


def test_fetch_page_raises_after_exhausting_retries(monkeypatch):
    fake = FakeRequests([FakeResponse(status_code=503)] * 3)
    monkeypatch.setattr(eia_api, "requests", fake)
    monkeypatch.setattr(eia_api.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="failed after 3 retries"):
        eia_api.fetch_page(BASE_URL, "respondent", "PJM", "s", "e", 0, "k")


def test_fetch_page_raises_on_non_transient_error(monkeypatch):
    # 429 (rate limit) is deliberately NOT swallowed — the daily run treats it as a
    # per-unit skip; only the backfill wrapper (backfill/*/fetch.py) backs off on it.
    fake = FakeRequests([FakeResponse(status_code=429)])
    monkeypatch.setattr(eia_api, "requests", fake)

    with pytest.raises(real_requests.HTTPError):
        eia_api.fetch_page(BASE_URL, "respondent", "PJM", "s", "e", 0, "k")
