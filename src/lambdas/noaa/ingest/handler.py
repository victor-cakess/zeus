from client import fetch_unit
from schema import SCHEMA, normalize_row
from shared import ingest, time_window

CONFIG = ingest.config_from_env()


def lambda_handler(event, context) -> dict:
    return ingest.run_ingest(
        config=CONFIG,
        units=event["units"],
        window_fn=time_window.lookback_window_dates,
        fetch_fn=fetch_unit,
        schema=SCHEMA,
        normalize_row=normalize_row,
    )
