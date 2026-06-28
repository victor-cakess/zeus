# dashboard/

Demo consumption layer over the Zeus dbt marts — a single Streamlit app that
reads `MARTS.FCT_*` and renders charts across four tabs. Read-only.

Like `docs/architecture.py`, this is **not** a project dependency: it's pulled in
on demand via `uv run --with`, so it never ships in any Lambda. Auth reuses the
key-pair transformer credentials (`sf_transformer.p8`, gitignored).

A **sidebar** holds the global controls — balancing authority + date range — that
drive the Generation and Prices tabs; the Weather tab keeps its own season + year
picker. A **data-health banner** at the top shows per-source freshness and coverage.

## Run

From the repo root:

```bash
export SNOWFLAKE_ACCOUNT=<org>-<account>
export SNOWFLAKE_PRIVATE_KEY_FILE="$(pwd)/sf_transformer.p8"

uv run --with streamlit --with snowflake-connector-python --with pandas \
    --with numpy --with altair --no-project streamlit run dashboard/app.py
```

Opens at http://localhost:8501. `SNOWFLAKE_USER/ROLE/DATABASE/WAREHOUSE` default
to the transformer values (same as `transform/profiles.yml`); only `ACCOUNT` and
the key file are required.

## What it shows

**Generation tab**
- **Daily generation** — `fct_energy_daily`, net MWh + renewable share over the date range.
- **Intraday profile — the duck curve** — `fct_generation_hourly`, average generation by hour of day (UTC): the midday renewable hump and evening net-load ramp.

**Weather tab** (own season + year picker)
- **Weather ⨝ generation — the D−1 lag** — `tmax` vs gross MWh, same-day next to prior-day (D−1), each with Pearson r and an OLS fit line. The lagged join exposes the stronger thermal-inertia signal documented in M-7 (0.76 same-day → 0.83 prior-day for ERCO summer). Pick a **season** (Spring/Summer/Fall/Winter/Full year) and one-or-more **years** (2020–2026); multiple years pool into one fit, colored by year.
- **Expected vs. actual** — least-squares baseline from the prior-day relationship, plotted **by day of season** so each selected year is one continuous overlaid curve (solid = actual, dashed = expected), + a table of the days generation deviated most from what temperature predicted. A scenario baseline (explains the past), not a forecast.

**Prices tab**
- **National fuel & energy prices** — `fct_fuel_prices_daily`, pick any FRED series.
- **Price ↔ demand** — FRED price (national) ⨝ EIA demand (per-BA net MWh) on `date`, dual-axis: does demand track the price or move independently?

**Data health tab** — per-source freshness, BA coverage on the latest day, the last fully-complete day, and the exclusions the weather charts apply (partial days + ~3-day weather lag).

Charts are [Altair](https://altair-viz.github.io/) (fit lines, tooltips, dual axes); simple time series stay Streamlit built-ins.
