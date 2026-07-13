"""CLI entry point for the EIA interchange-data (BA-to-BA flows) historical backfill.

    uv run python run.py extract   --start 2019-01-01 [--end YYYY-MM-DD] [--concurrency N]
    uv run python run.py transform --start 2019-01-01 [--end YYYY-MM-DD] [--concurrency N]

The v2 API route serves data from 2019-01-01 only (its startPeriod — same floor
as the region-data route) — EIA-930's earlier 2015-2018 history exists solely as
bulk CSV downloads, not through this route. Empty (BA, year) ranges are logged and
skipped, so an earlier --start is safe, just wasted probes.

Config via env: EIA_API_KEY (required for extract; same key as the fuel-type
backfill), BUCKET (default zeus-dev-energy-data), SOURCE (default eia_interchange).
"""

import argparse
import os
import sys
from datetime import date, datetime, timezone

import _bootstrap  # noqa: F401  (sets sys.path)
import extract
import logconf
import transform
import units


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="EIA interchange-data historical backfill")
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("extract", "transform"):
        p = sub.add_parser(phase)
        p.add_argument("--start", required=True, type=date.fromisoformat,
                       help="inclusive start date YYYY-MM-DD")
        p.add_argument("--end", type=date.fromisoformat,
                       default=datetime.now(timezone.utc).date(),
                       help="inclusive end date YYYY-MM-DD (default: today UTC)")
        p.add_argument("--concurrency", type=int, default=10)
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.start > args.end:
        sys.exit(f"--start {args.start} is after --end {args.end}")

    api_key = os.environ.get("EIA_API_KEY")
    if args.phase == "extract" and not api_key:
        sys.exit("EIA_API_KEY env var is required for extract")

    bucket = os.environ.get("BUCKET", "zeus-dev-energy-data")
    source = os.environ.get("SOURCE", "eia_interchange")
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
        extract.run(units.BALANCING_AUTHORITIES, args.start, args.end,
                    bucket, source, api_key, args.concurrency, logger)
    else:
        transform.run(args.start, args.end, bucket, source, args.concurrency, logger)

    logger.info("%s done", args.phase)


if __name__ == "__main__":
    main()
