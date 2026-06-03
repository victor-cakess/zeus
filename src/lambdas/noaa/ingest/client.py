import time

import requests

BASE_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
DATA_TYPES = "TMAX,TMIN,TAVG,PRCP,SNOW,SNWD,AWND,WSF2,WSF5,WDF2,RHAV,ASLP,ADPT"

# Balancing authority → its weather stations. The BA codes match EIA's so weather
# joins to grid data on `ba` downstream; every fetched row is tagged with its BA.
STATIONS = {
    "CISO": [
        "USW00023234",  # San Francisco SFO
        "USW00023174",  # Los Angeles LAX
        "USW00023188",  # San Diego
        "USW00023232",  # Sacramento
        "USW00023257",  # Fresno
        "USW00023155",  # Bakersfield
        "USW00023129",  # Santa Barbara
        "USW00023185",  # San Jose
        "USW00023272",  # Stockton
        "USW00093193",  # Redding
    ],
    "PJM": [
        "USW00093738",  # Washington Dulles
        "USW00014734",  # Philadelphia
        "USW00014735",  # Pittsburgh
        "USW00014733",  # Baltimore
        "USW00014895",  # Cleveland
        "USW00014820",  # Detroit
        "USW00013739",  # Richmond
        "USW00014751",  # Chicago O'Hare
        "USW00014778",  # Columbus
        "USW00013781",  # Roanoke
    ],
    "ERCO": [
        "USW00003927",  # Dallas Love Field
        "USW00012960",  # Houston
        "USW00012921",  # San Antonio
        "USW00013958",  # Austin
        "USW00012919",  # El Paso
        "USW00003928",  # Fort Worth
        "USW00012924",  # Corpus Christi
        "USW00013957",  # Lubbock
        "USW00003900",  # Amarillo
        "USW00012906",  # Brownsville
    ],
    "MISO": [
        "USW00094846",  # Chicago O'Hare
        "USW00014922",  # Minneapolis
        "USW00014733",  # Indianapolis
        "USW00013994",  # St. Louis
        "USW00014847",  # Detroit Metro
        "USW00013963",  # Kansas City
        "USW00014933",  # Milwaukee
        "USW00013897",  # Memphis
        "USW00014836",  # Omaha
        "USW00014914",  # Fargo
    ],
}


def fetch_unit(unit: str, start: str, end: str, retries: int = 3) -> list[dict]:
    """Fetch all stations for a BA in a single batched NCEI request (comma-separated
    stations param), tag each row with the BA, and return the flat list. One request
    per BA is what the NCEI API supports and what avoids per-station throttling."""
    params = {
        "dataset": "daily-summaries",
        "dataTypes": DATA_TYPES,
        "stations": ",".join(STATIONS[unit]),
        "startDate": start,
        "endDate": end,
        "format": "json",
        "units": "metric",
    }
    for attempt in range(retries):
        response = requests.get(BASE_URL, params=params, timeout=120)
        if response.status_code in (502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        # NCEI returns an empty body (not '[]') when a BA has no data for the window.
        rows = response.json() if response.text.strip() else []
        for row in rows:
            row["ba"] = unit
        return rows
    raise RuntimeError(f"failed after {retries} retries: {unit}")
