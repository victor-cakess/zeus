from shared import eia_api

# interchange-data: hourly BA-to-BA power flows, signed (positive = fromba
# exports to toba). Fan-out on fromba only — one request-loop per BA returns
# flows to all of its neighbors; the mirrored toba→fromba rows arrive via the
# neighbor's own fan-out unit, and both directions landing is deliberate (the
# asymmetry test compares them). Same API + key as the other EIA clients.
BASE_URL = "https://api.eia.gov/v2/electricity/rto/interchange-data/data/"
FACET = "fromba"


def fetch_unit(unit: str, start: str, end: str, api_key: str) -> list[dict]:
    return eia_api.fetch_unit(BASE_URL, FACET, unit, start, end, api_key)
