import time

import requests
from schema import DATA_TYPES

BASE_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
# Single source of truth for the datatype set is schema.DATA_TYPES; the NCEI request
# wants it as a comma-separated string.
DATA_TYPES_PARAM = ",".join(DATA_TYPES)

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
    "ISNE": [
        "USW00014739",  # Boston, MA
        "USW00014765",  # Providence, RI
        "USW00014742",  # Burlington, VT
        "USW00014740",  # Hartford-Bradley, CT
        "USW00014758",  # New Haven, CT
        "USW00014710",  # Manchester, NH
        "USW00014745",  # Concord, NH
        "USW00014606",  # Bangor, ME
        "USW00014605",  # Augusta, ME
    ],
    "NYIS": [
        "USW00094728",  # NYC Central Park
        "USW00094789",  # JFK
        "USW00014732",  # LaGuardia
        "USW00014733",  # Buffalo
        "USW00014768",  # Rochester
        "USW00014771",  # Syracuse
        "USW00004724",  # Niagara Falls
        "USW00004725",  # Binghamton
        "USW00004781",  # Islip-LI MacArthur
    ],
    "SWPP": [
        "USW00013968",  # Tulsa, OK
        "USW00003954",  # Oklahoma City Wiley Post, OK
        "USW00003928",  # Wichita, KS
        "USW00013967",  # Olathe/Kansas City, KS
        "USW00014939",  # Lincoln, NE
        "USW00013976",  # Lafayette, LA
    ],
    "TVA": [
        "USW00013891",  # Knoxville, TN
        "USW00013897",  # Nashville, TN
        "USW00013893",  # Memphis, TN
        "USW00003856",  # Huntsville, AL
        "USW00003883",  # Birmingham, AL
        "USW00003847",  # Crossville, TN
    ],
    "SOCO": [
        "USW00013874",  # Atlanta, GA
        "USW00003822",  # Savannah, GA
        "USW00003820",  # Augusta, GA
        "USW00003813",  # Macon, GA
        "USW00003883",  # Birmingham, AL
        "USW00003856",  # Huntsville, AL
    ],
    "DUK": [
        "USW00003812",  # Asheville, NC
        "USW00003810",  # Hickory, NC
        "USW00013748",  # Wilmington, NC
    ],
    "FPL": [
        "USW00012839",  # Miami, FL
        "USW00012836",  # Key West, FL
        "USW00012844",  # West Palm Beach, FL
        "USW00012843",  # Vero Beach, FL
        "USW00012838",  # Melbourne, FL
    ],
    "BPAT": [
        "USW00024229",  # Portland, OR
        "USW00024233",  # Seattle-Tacoma, WA
        "USW00024157",  # Spokane, WA
        "USW00024144",  # Great Falls, MT
        "USW00024131",  # Boise, ID
        "USW00024243",  # Wenatchee, WA
    ],
    "PSCO": [
        "USW00003017",  # Denver, CO
        "USW00023066",  # Grand Junction, CO
        "USW00023061",  # Alamosa, CO
    ],
    "SRP": [
        "USW00023183",  # Phoenix Sky Harbor, AZ
        "USW00003184",  # Phoenix Deer Valley, AZ
        "USW00003185",  # Mesa Falcon Field, AZ
        "USW00003192",  # Scottsdale, AZ
        "USW00003162",  # Page, AZ
    ],
}


def fetch_unit(unit: str, start: str, end: str, retries: int = 3) -> list[dict]:
    """Fetch all stations for a BA in a single batched NCEI request (comma-separated
    stations param), tag each row with the BA, and return the flat list. One request
    per BA is what the NCEI API supports and what avoids per-station throttling."""
    params = {
        "dataset": "daily-summaries",
        "dataTypes": DATA_TYPES_PARAM,
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
