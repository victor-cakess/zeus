"""CLI entry point for the NOAA historical backfill.

    uv run python run.py extract   --start 2010-01-01 [--end YYYY-MM-DD] [--concurrency N]
    uv run python run.py transform --start 2010-01-01 [--end YYYY-MM-DD] [--concurrency N]

Then load into Snowflake with snowflake_load.py (whole-stage COPY). NOAA's NCEI
endpoint needs no API key. Config via env: BUCKET (default zeus-dev-energy-data),
SOURCE (default noaa).
"""

import argparse
import os
import socket
import sys
from datetime import date, datetime, timezone

import urllib3.util.connection as urllib3_cn

import _bootstrap  # noqa: F401  (sets sys.path)
import client
import extract
import logconf
import transform

# Force IPv4 for all NCEI requests. This machine has a broken IPv6 route to
# ncei.noaa.gov (it publishes both A and AAAA records); urllib3 prefers IPv6 and
# stalls ~10 min/request on the dead route. Forcing IPv4 → ~2s/request. Local-only:
# the NOAA Lambda is unaffected (AWS network path works). See ncei-ipv6-findings.md.
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET


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
        # extract = one wide request per BA (all its stations batched). With IPv4
        # forced (see top of file) each request is ~2s; NCEI tolerates concurrency,
        # so default 4 runs the BAs in parallel waves. transform is S3-only → 10.
        p.add_argument("--concurrency", type=int,
                       default=4 if phase == "extract" else 10,
                       help="parallel workers (extract default 4: one NCEI request "
                            "per BA, run in parallel; transform default 10: S3-only)")
        p.add_argument("--overwrite", action="store_true",
                       help="re-fetch/re-write keys that already exist instead of "
                            "skipping them (default: skip for idempotent resume). Use "
                            "when adding BAs to an already-backfilled range.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.start > args.end:
        sys.exit(f"--start {args.start} is after --end {args.end}")

    bucket = os.environ.get("BUCKET", "zeus-dev-energy-data")
    source = os.environ.get("SOURCE", "noaa")
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
        extract.run(list(client.STATIONS), args.start, args.end,
                    bucket, source, args.concurrency, logger, args.overwrite)
    else:
        transform.run(args.start, args.end, bucket, source, args.concurrency, logger, args.overwrite)

    logger.info("%s done", args.phase)


if __name__ == "__main__":
    main()
