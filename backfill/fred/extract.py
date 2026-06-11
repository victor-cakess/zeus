"""Phase A: fetch full history per series in ONE FRED request, split rows by day,
write one raw JSON per (series, day) — identical layout to the daily pipeline. The
partition is backdated to each row's observation date (raw["date"]).

The backfill fetches each series' whole range in a single request — FRED returns
up to 100k observations per call, and 2014→today is ~3,100 business days, so no
pagination is needed. The production client already drops FRED's "." placeholder
observations and tags each row with series + series_id.

Idempotency: list the existing raw keys in range once, skip puts for keys already
present. A series that returns no rows writes nothing (re-probed cheaply on resume).
"""

import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import _bootstrap  # noqa: F401  (sets sys.path)
from fetch import fetch_with_retry
from shared import paths, s3_io
from tqdm import tqdm


def _extract_unit(unit, start: date, end: date, existing, bucket, source, api_key, logger) -> Counter:
    logger.info("FETCHING unit=%s window=%s..%s", unit, start, end)
    t0 = time.monotonic()
    rows = fetch_with_retry(unit, start.isoformat(), end.isoformat(), api_key, logger, unit)
    elapsed = time.monotonic() - t0
    if not rows:
        logger.info("EMPTY unit=%s elapsed=%.1fs", unit, elapsed)
        return Counter(empty_units=1)

    by_day: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[date.fromisoformat(row["date"])].append(row)

    written = skipped = 0
    for day, day_rows in by_day.items():
        key = paths.raw_key(source, unit, day)
        if key in existing:
            skipped += 1
            continue
        s3_io.put_json(bucket, key, day_rows)
        written += 1

    logger.info(
        "OK unit=%s rows=%d days=%d written=%d skipped=%d elapsed=%.1fs",
        unit, len(rows), len(by_day), written, skipped, elapsed,
    )
    return Counter(units_with_data=1, rows=len(rows), raw_written=written, raw_skipped=skipped)


def run(units, start: date, end: date, bucket, source, api_key, concurrency, logger, overwrite=False):
    # Idempotency: one pass listing every existing raw key in range (union per year).
    # With --overwrite, skip the listing entirely so every key is (re-)written.
    existing = set()
    if not overwrite:
        for year in range(start.year, end.year + 1):
            existing |= set(s3_io.list_keys(bucket, f"raw/{source}/ingestion_year={year:04d}/"))
    logger.info("RANGE %s..%s units=%d existing_keys=%d overwrite=%s",
                start, end, len(units), len(existing), overwrite)

    overall = Counter()
    # One bar over the series (each is a single request) — the "is it working"
    # signal. Per-request detail (FETCHING/OK/EMPTY/RETRY) goes to the log file.
    bar = tqdm(total=len(units), desc="extract", unit="series")
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(_extract_unit, unit, start, end, existing, bucket, source, api_key, logger): unit
            for unit in units
        }
        for future in as_completed(futures):
            unit = futures[future]
            try:
                overall += future.result()
            except Exception:
                logger.exception("FAILED unit=%s", unit)
                overall["failed_units"] += 1
                tqdm.write(f"  {unit} FAILED — see log")
            bar.update(1)
            bar.set_postfix(written=overall["raw_written"], failed=overall["failed_units"])
    bar.close()

    logger.info(
        "EXTRACT SUMMARY units_with_data=%d empty=%d failed=%d "
        "raw_written=%d raw_skipped=%d rows=%d",
        overall["units_with_data"], overall["empty_units"], overall["failed_units"],
        overall["raw_written"], overall["raw_skipped"], overall["rows"],
    )
    print(
        f"\nEXTRACT DONE — {overall['raw_written']} objects written, "
        f"{overall['raw_skipped']} skipped, {overall['empty_units']} empty, "
        f"{overall['failed_units']} failed",
        flush=True,
    )
