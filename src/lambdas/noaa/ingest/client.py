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
        "USW00093193",  # Fresno
        "USW00023155",  # Bakersfield
        "USW00023190",  # Santa Barbara
        "USW00023293",  # San Jose
        "USW00023237",  # Stockton
        "USW00024257",  # Redding
    ],
    "PJM": [
        "USW00093738",  # Washington Dulles
        "USW00013739",  # Philadelphia
        "USW00094823",  # Pittsburgh
        "USW00093721",  # Baltimore
        "USW00014820",  # Cleveland
        "USW00013740",  # Richmond
        "USW00094846",  # Chicago O'Hare
        "USW00014821",  # Columbus
        "USW00013741",  # Roanoke
    ],
    "ERCO": [
        "USW00003927",  # Dallas Love Field
        "USW00012960",  # Houston
        "USW00012921",  # San Antonio
        "USW00013958",  # Austin
        "USW00023044",  # El Paso
        "USW00013961",  # Fort Worth
        "USW00012924",  # Corpus Christi
        "USW00023042",  # Lubbock
        "USW00023047",  # Amarillo
        "USW00012919",  # Brownsville
    ],
    "MISO": [
        "USW00014922",  # Minneapolis
        "USW00093819",  # Indianapolis
        "USW00013994",  # St. Louis
        "USW00094847",  # Detroit Metro
        "USW00003947",  # Kansas City
        "USW00014839",  # Milwaukee
        "USW00013893",  # Memphis
        "USW00014942",  # Omaha
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
        "USW00003967",  # Olathe/Kansas City, KS
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
        "USW00024143",  # Great Falls, MT
        "USW00024131",  # Boise, ID
        "USW00094239",  # Wenatchee, WA
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
