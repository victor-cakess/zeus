"""CLI entry point for the FRED historical backfill.

    FRED_API_KEY=... uv run python run.py extract   --start 2014-01-01 [--end YYYY-MM-DD] [--concurrency N]
    uv run python run.py transform --start 2014-01-01 [--end YYYY-MM-DD] [--concurrency N]

Then load into Snowflake with snowflake_load.py (whole-stage COPY). Config via env:
FRED_API_KEY (required for extract), BUCKET (default zeus-dev-energy-data),
SOURCE (default fred).
"""

import argparse
import os
import sys
from datetime import date, datetime, timezone

import _bootstrap  # noqa: F401  (sets sys.path)
import client
import extract
import logconf
import transform


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="FRED historical backfill")
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("extract", "transform"):
        p = sub.add_parser(phase)
        p.add_argument("--start", required=True, type=date.fromisoformat,
                       help="inclusive start date YYYY-MM-DD")
        p.add_argument("--end", type=date.fromisoformat,
                       default=datetime.now(timezone.utc).date(),
                       help="inclusive end date YYYY-MM-DD (default: today UTC)")
        # extract = one FRED request per series (whole range, ~2s each; FRED allows
        # ~120 req/min so 5 parallel is comfortable). transform is S3-only → 10.
        p.add_argument("--concurrency", type=int,
                       default=5 if phase == "extract" else 10,
                       help="parallel workers (extract default 5: one FRED request "
                            "per series; transform default 10: S3-only)")
        p.add_argument("--overwrite", action="store_true",
                       help="re-fetch/re-write keys that already exist instead of "
                            "skipping them (default: skip for idempotent resume). Use "
                            "when adding series to an already-backfilled range.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.start > args.end:
        sys.exit(f"--start {args.start} is after --end {args.end}")

    bucket = os.environ.get("BUCKET", "zeus-dev-energy-data")
    source = os.environ.get("SOURCE", "fred")
    api_key = os.environ.get("FRED_API_KEY")
    if args.phase == "extract" and not api_key:
        sys.exit("FRED_API_KEY env var is required for extract")

    logger, log_file = logconf.configure(args.phase)
    logger.info(
        "%s start=%s end=%s concurrency=%d bucket=%s source=%s overwrite=%s",
        args.phase, args.start, args.end, args.concurrency, bucket, source, args.overwrite,
    )
    print(
        f"{args.phase}: {args.start} → {args.end}"
        f"{'  [OVERWRITE]' if args.overwrite else ''}  (detailed log → {log_file})",
        flush=True,
    )

    if args.phase == "extract":
        extract.run(list(client.SERIES), args.start, args.end,
                    bucket, source, api_key, args.concurrency, logger, args.overwrite)
    else:
        transform.run(args.start, args.end, bucket, source, args.concurrency, logger, args.overwrite)

    logger.info("%s done", args.phase)


if __name__ == "__main__":
    main()
