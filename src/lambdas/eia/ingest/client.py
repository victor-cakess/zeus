from shared import eia_api

# fuel-type-data: hourly net generation per balancing authority per fuel type.
BASE_URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
FACET = "respondent"


def fetch_unit(unit: str, start: str, end: str, api_key: str) -> list[dict]:
    return eia_api.fetch_unit(BASE_URL, FACET, unit, start, end, api_key)
