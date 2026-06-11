import time

import requests

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Unit slug → FRED series id. The slug is the fan-out key passed in {"units": [...]};
# every fetched row is tagged with it so the (series, date) grain key is present.
# National-level price series — no `ba` key; they join to grid data downstream on
# date. Each series has its own native frequency (noted below); the rolling lookback
# window re-captures late publications and revisions regardless of frequency.
# infra/pipelines/fred/locals.tf mirrors this key set — keep in sync.
SERIES = {
    # Daily spot prices (business days)
    "WTI": "DCOILWTICO",           # WTI crude oil, Cushing OK, USD/bbl
    "BRENT": "DCOILBRENTEU",       # Brent crude oil, Europe, USD/bbl
    "HENRYHUB": "DHHNGSP",         # Henry Hub natural gas, USD/MMBtu
    "HEATINGOIL": "DHOILNYH",      # No. 2 heating oil, NY Harbor, USD/gal
    "PROPANEMT": "DPROPANEMBTX",   # Propane, Mont Belvieu TX, USD/gal
    "JETFUEL": "DJFUELUSGULF",     # Kerosene-type jet fuel, US Gulf Coast, USD/gal
    "GASNYH": "DGASNYH",           # Conventional gasoline, NY Harbor, USD/gal
    "GASGULF": "DGASUSGULF",       # Conventional gasoline, US Gulf Coast, USD/gal
    # Weekly retail prices (week ending Monday)
    "GASOLINE": "GASREGW",         # US regular gasoline retail, USD/gal
    "DIESEL": "GASDESW",           # US diesel retail, USD/gal
    # Monthly indexes (revised by BLS up to ~4 months after first release)
    "COALPPI": "WPU0571",          # PPI: coal, index 1982=100
    "NATGASPPI": "WPU0531",        # PPI: natural gas, index 1982=100
    "ELECPPI": "WPU054",           # PPI: electric power, index 1982=100
    "ELECPRICE": "APU000072610",   # Avg price: electricity per kWh, US city average
    "CPIENERGY": "CPIENGSL",       # CPI: energy, US city average, index 1982-84=100
}


def fetch_unit(unit: str, start: str, end: str, api_key: str, retries: int = 3) -> list[dict]:
    """Fetch one FRED series' observations for the window and return the flat list,
    each row tagged with its unit slug + series id. FRED marks missing observations
    (weekends, holidays, not-yet-published) with value "." — those are dropped here,
    so a series with no real observations in the window returns [] (a skip, not a
    failure). No pagination: a 150-day window is ≤ ~110 rows, far under FRED's
    100k-observation limit."""
    params = {
        "api_key": api_key,
        "series_id": SERIES[unit],
        "observation_start": start,
        "observation_end": end,
        "file_type": "json",
    }
    for attempt in range(retries):
        response = requests.get(BASE_URL, params=params, timeout=60)
        if response.status_code in (502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        rows = [o for o in response.json()["observations"] if o["value"] != "."]
        for row in rows:
            row["series"] = unit
            row["series_id"] = SERIES[unit]
        return rows
    raise RuntimeError(f"failed after {retries} retries: {unit}")
