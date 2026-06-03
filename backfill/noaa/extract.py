"""Phase A: fetch full history per BA (its station list), split rows by day, write
one raw JSON per (BA, day) — identical layout to the daily pipeline. The partition
is backdated to each row's observation date (raw["DATE"]).

Idempotency: list each year's raw prefix once, skip puts for keys already present.
Empty (BA, year) ranges write nothing (and are re-probed cheaply on resume).
"""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import _bootstrap  # noqa: F401  (sets sys.path)
from fetch import fetch_with_retry
from shared import paths, s3_io


def _year_window(year: int, start: date, end: date) -> tuple[str, str]:
    """NCEI start/end (YYYY-MM-DD) for `year`, clamped to the overall range."""
    lo = max(date(year, 1, 1), start)
    hi = min(date(year, 12, 31), end)
    return lo.isoformat(), hi.isoformat()


def _extract_unit_year(unit, year, window, existing, bucket, source, logger) -> Counter:
    start, end = window
    print(f"  processing {unit} {year}...", flush=True)
    rows = fetch_with_retry(unit, start, end)
    if not rows:
        logger.info("EMPTY unit=%s year=%s", unit, year)
        print(f"  {unit} {year} done (no data)", flush=True)
        return Counter(empty_units=1)

    by_day: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[date.fromisoformat(row["DATE"])].append(row)

    written = skipped = 0
    for day, day_rows in by_day.items():
        key = paths.raw_key(source, unit, day)
        if key in existing:
            skipped += 1
            continue
        s3_io.put_json(bucket, key, day_rows)
        written += 1

    logger.info(
        "OK unit=%s year=%s rows=%d days=%d written=%d skipped=%d",
        unit, year, len(rows), len(by_day), written, skipped,
    )
    print(f"  {unit} {year} done ({written} days, {len(rows)} rows)", flush=True)
    return Counter(units_with_data=1, rows=len(rows), raw_written=written, raw_skipped=skipped)


def run(units, start: date, end: date, bucket, source, concurrency, logger):
    overall = Counter()
    for year in range(start.year, end.year + 1):
        window = _year_window(year, start, end)
        existing = set(s3_io.list_keys(bucket, f"raw/{source}/ingestion_year={year:04d}/"))
        logger.info("YEAR %s window=%s existing_keys=%d", year, window, len(existing))
        print(f"[{year}] {len(units)} BAs ({window[0]} .. {window[1]})", flush=True)

        counts = Counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(
                    _extract_unit_year,
                    unit, year, window, existing, bucket, source, logger,
                ): unit
                for unit in units
            }
            for future in as_completed(futures):
                unit = futures[future]
                try:
                    counts += future.result()
                except Exception:
                    logger.exception("FAILED unit=%s year=%s", unit, year)
                    counts["failed_units"] += 1
                    print(f"  {unit} {year} FAILED — see log", flush=True)

        logger.info(
            "YEAR %s SUMMARY units_with_data=%d empty=%d failed=%d "
            "raw_written=%d raw_skipped=%d rows=%d",
            year, counts["units_with_data"], counts["empty_units"], counts["failed_units"],
            counts["raw_written"], counts["raw_skipped"], counts["rows"],
        )
        print(
            f"[{year}] done — {counts['raw_written']} written, "
            f"{counts['empty_units']} empty, {counts['failed_units']} failed",
            flush=True,
        )
        overall += counts

    logger.info(
        "EXTRACT SUMMARY years=%d units_with_data=%d empty=%d failed=%d "
        "raw_written=%d raw_skipped=%d rows=%d",
        end.year - start.year + 1, overall["units_with_data"], overall["empty_units"],
        overall["failed_units"], overall["raw_written"], overall["raw_skipped"], overall["rows"],
    )
    print(
        f"\nEXTRACT DONE — {overall['raw_written']} objects written, "
        f"{overall['raw_skipped']} skipped, {overall['empty_units']} empty, "
        f"{overall['failed_units']} failed",
        flush=True,
    )
