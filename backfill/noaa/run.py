"""CLI entry point for the NOAA historical backfill.

    uv run python run.py extract   --start 2010-01-01 [--end YYYY-MM-DD] [--concurrency N]
    uv run python run.py transform --start 2010-01-01 [--end YYYY-MM-DD] [--concurrency N]

Then load into Snowflake with snowflake_load.py (whole-stage COPY). NOAA's NCEI
endpoint needs no API key. Config via env: BUCKET (default zeus-dev-energy-data),
SOURCE (default noaa).
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
    parser = argparse.ArgumentParser(description="NOAA historical backfill")
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("extract", "transform"):
        p = sub.add_parser(phase)
        p.add_argument("--start", required=True, type=date.fromisoformat,
                       help="inclusive start date YYYY-MM-DD")
        p.add_argument("--end", type=date.fromisoformat,
                       default=datetime.now(timezone.utc).date(),
                       help="inclusive end date YYYY-MM-DD (default: today UTC)")
        p.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.start > args.end:
        sys.exit(f"--start {args.start} is after --end {args.end}")

    bucket = os.environ.get("BUCKET", "zeus-dev-energy-data")
    source = os.environ.get("SOURCE", "noaa")
    logger, log_file = logconf.configure(args.phase)
    logger.info(
        "%s start=%s end=%s concurrency=%d bucket=%s source=%s",
        args.phase, args.start, args.end, args.concurrency, bucket, source,
    )
    print(
        f"{args.phase}: {args.start} → {args.end}  (detailed log → {log_file})",
        flush=True,
    )

    if args.phase == "extract":
        extract.run(list(client.STATIONS), args.start, args.end,
                    bucket, source, args.concurrency, logger)
    else:
        transform.run(args.start, args.end, bucket, source, args.concurrency, logger)

    logger.info("%s done", args.phase)


if __name__ == "__main__":
    main()
