"""File logging for backfill runs: a timestamped file under logs/.

The full detail (every OK/EMPTY/SKIP/SUMMARY line, by BA and day) goes to the file
only. Simple progress for the terminal is printed directly by the phases.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent / "logs"


def configure(phase: str) -> tuple[logging.Logger, Path]:
    _LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_file = _LOG_DIR / f"{phase}-{ts}.log"

    logger = logging.getLogger("backfill.noaa")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger, log_file
