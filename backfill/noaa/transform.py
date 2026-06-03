"""Phase B: per day, consolidate that day's raw JSON files (all BAs) into one
Snappy Parquet in the curated layer — same schema/normalization as the daily
ingest Lambda, minus its report/SNS/alert logic.

Idempotency: list each year's curated prefix once, skip days whose parquet exists.
Days with no raw data write nothing.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import BytesIO

import _bootstrap  # noqa: F401  (sets sys.path)
import pyarrow as pa
import pyarrow.parquet as pq
from schema import SCHEMA, normalize_row
from shared import paths, s3_io


def _days_in_year(year: int, start: date, end: date):
    day = max(date(year, 1, 1), start)
    last = min(date(year, 12, 31), end)
    while day <= last:
        yield day
        day += timedelta(days=1)


def _transform_day(day, existing, bucket, source, logger) -> Counter:
    out_key = paths.curated_prefix(source, day) + f"{source}_grid.parquet"
    if out_key in existing:
        logger.info("SKIP day=%s (parquet exists)", day)
        return Counter(skipped=1)

    rows = [
        normalize_row(r, day)
        for obj in s3_io.iter_objects(bucket, paths.raw_prefix(source, day))
        for r in obj
    ]
    if not rows:
        logger.info("EMPTY day=%s", day)
        return Counter(empty=1)

    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    buf = BytesIO()
    pq.write_table(table, buf, compression="snappy")
    s3_io.put_bytes(bucket, out_key, buf.getvalue())
    logger.info("OK day=%s rows=%d key=%s", day, len(rows), out_key)
    print(f"  {day} done ({len(rows)} rows)", flush=True)
    return Counter(written=1, rows=len(rows))


def run(start: date, end: date, bucket, source, concurrency, logger):
    overall = Counter()
    for year in range(start.year, end.year + 1):
        existing = set(
            s3_io.list_keys(bucket, f"curated/{source}/ingestion_year={year:04d}/")
        )
        logger.info("YEAR %s existing_parquets=%d", year, len(existing))
        print(f"[{year}] transforming days...", flush=True)

        counts = Counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(_transform_day, day, existing, bucket, source, logger): day
                for day in _days_in_year(year, start, end)
            }
            for future in as_completed(futures):
                day = futures[future]
                try:
                    counts += future.result()
                except Exception:
                    logger.exception("FAILED day=%s", day)
                    counts["failed"] += 1
                    print(f"  {day} FAILED — see log", flush=True)

        logger.info(
            "YEAR %s SUMMARY parquets_written=%d skipped=%d empty=%d failed=%d rows=%d",
            year, counts["written"], counts["skipped"], counts["empty"],
            counts["failed"], counts["rows"],
        )
        print(
            f"[{year}] done — {counts['written']} parquets, "
            f"{counts['skipped']} skipped, {counts['empty']} empty, {counts['failed']} failed",
            flush=True,
        )
        overall += counts

    logger.info(
        "TRANSFORM SUMMARY years=%d parquets_written=%d skipped=%d empty=%d failed=%d rows=%d",
        end.year - start.year + 1, overall["written"], overall["skipped"],
        overall["empty"], overall["failed"], overall["rows"],
    )
    print(
        f"\nTRANSFORM DONE — {overall['written']} parquets written, "
        f"{overall['skipped']} skipped, {overall['empty']} empty, {overall['failed']} failed",
        flush=True,
    )
