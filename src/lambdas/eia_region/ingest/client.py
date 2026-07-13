from shared import eia_api

# region-data: hourly Demand (D), Day-ahead demand forecast (DF), Net generation
# (NG), and Total interchange (TI) per balancing authority. No type facet — one
# request-loop per BA returns all four series; the type split is a column, not a
# fan-out unit. Same API + key as the EIA fuel-type client.
BASE_URL = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
FACET = "respondent"


def fetch_unit(unit: str, start: str, end: str, api_key: str) -> list[dict]:
    return eia_api.fetch_unit(BASE_URL, FACET, unit, start, end, api_key)
