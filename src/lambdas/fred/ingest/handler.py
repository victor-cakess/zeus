import os

from client import fetch_unit
from schema import SCHEMA, normalize_row
from shared import ingest, ssm, time_window

CONFIG = ingest.config_from_env()
API_KEY_SSM_PATH = os.environ["API_KEY_SSM_PATH"]


def lambda_handler(event, context) -> dict:
    # Resolve the API key once before the pool — ssm.get_parameter caches, so this
    # avoids a race on the module-level cache across workers. The closure adapts the
    # FRED client's (unit, start, end, api_key) signature to the orchestrator's
    # (unit, start, end) fetch contract.
    api_key = ssm.get_parameter(API_KEY_SSM_PATH)
    return ingest.run_ingest(
        config=CONFIG,
        units=event["units"],
        window_fn=time_window.lookback_window_dates,
        fetch_fn=lambda unit, start, end: fetch_unit(unit, start, end, api_key),
        schema=SCHEMA,
        normalize_row=normalize_row,
    )
