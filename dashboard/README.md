# dashboard/

Demo consumption layer over the Zeus dbt models — a single Streamlit app that reads
the **`REPORTING.VW_*` views only** (never the marts or the landing schemas) and
renders charts across five tabs. Read-only; it writes nothing.

Like `docs/architecture.py`, this is **not** a project dependency: it's pulled in
on demand via `uv run --with`, so it never ships in any Lambda.

A **sidebar** holds the global controls — balancing authority + date range — that
drive the Generation, Prices, and Operators tabs; the Weather tab keeps its own
season + year picker. A **data-health banner** at the top shows per-source freshness and coverage.

## Governance — views-only, least privilege

The dashboard is internet-facing (Streamlit Community Cloud), so it connects as its
own least-privilege identity, **not** the transformer credentials the pipeline uses:

- **`ZEUS_DEV_DASHBOARD` service user** (key-pair auth) with the leaf
  **`ZEUS_DEV_DASHBOARD_ROLE`** — deliberately **not** rolled up into the SYSADMIN
  hierarchy. The role holds `SELECT` on the **`REPORTING` views only** — never on
  `MARTS`, the landing schemas, or any base table. The reporting views are 1:1
  pass-throughs over the marts, so the schema grant *is* the governance boundary:
  the public app physically cannot read anything but the serving layer.
- **Dedicated `ZEUS_DEV_DASHBOARD_WH` (x-small)**, isolated from the ingest/dbt
  warehouse and capped by a 25-credit/month resource monitor (suspend at 100%), with
  a 60 s statement timeout — a runaway public dashboard can't burn the account or
  slow the pipeline.
- **Private key lives in Streamlit secrets, not SSM.** Only the public key is in
  Terraform (via tfvars); nothing secret is in code or state.

The whole identity — role, user, warehouse, resource monitor, and grants (including
`SELECT` on *future* REPORTING views, so a newly added `vw_*` is authorized with no
second apply) — is provisioned in `infra/core/snowflake_dashboard.tf`; dbt builds the
views into the schema shell Terraform creates.

## Run

Locally (from repo root) — key from a file, connecting as the dashboard user:

```bash
export SNOWFLAKE_ACCOUNT=<org>-<account>
export SNOWFLAKE_PRIVATE_KEY_FILE="$(pwd)/sf_dashboard.p8"

uv run --with streamlit --with snowflake-connector-python --with pandas \
    --with numpy --with altair --no-project streamlit run dashboard/app.py
```

Opens at http://localhost:8501. `SNOWFLAKE_USER` / `ROLE` / `WAREHOUSE` / `DATABASE`
default to the dashboard identity (`ZEUS_DEV_DASHBOARD` / `ZEUS_DEV_DASHBOARD_ROLE` /
`ZEUS_DEV_DASHBOARD_WH` / `ZEUS_DEV`) and the schema is pinned to `REPORTING`, so only
`ACCOUNT` and the key file are required.

On **Streamlit Community Cloud** the account and PEM private key come from `st.secrets`
(`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_PRIVATE_KEY`); everything else defaults to the
dashboard identity above, so nothing else is required. See RUNBOOK.md → dashboard.

## What it shows

**Generation tab**
- **Renewable share** — `vw_energy_daily`, daily share of gross generation from solar + wind + hydro (ratio of sums, M-6): the decarbonization trend and seasonal rhythm.
- **Generation mix by fuel** — `vw_generation_by_fuel_daily`, daily gross MWh stacked by fuel source (M-19), one color per source. The 16 raw EIA-930 codes are grouped into ~9 recognizable buckets for display (Solar = SUN + SNB, Storage = battery + pumped-storage discharge, …); colors are pinned to the bucket so switching BA never repaints a band, and the bands sum to the day's total gross output (the renewable-share numerator sits inside it).
- **Intraday profile — the duck curve** — `vw_generation_hourly`, average generation by hour of day (UTC): the midday renewable hump and evening net-load ramp.

**Weather tab** (own season + year picker)
- **Weather ⨝ generation — the D−1 lag** — `tmax` vs gross MWh (both from `vw_energy_daily`), same-day next to prior-day (D−1), each with Pearson r and an OLS fit line. The lagged join exposes the stronger thermal-inertia signal documented in M-7 (0.76 same-day → 0.83 prior-day for ERCO summer). Pick a **season** (Spring/Summer/Fall/Winter/Full year) and one-or-more **years** (2020–2026); multiple years pool into one fit, colored by year.
- **Expected vs. actual** — least-squares baseline from the prior-day relationship, plotted **by day of season** so each selected year is one continuous overlaid curve (solid = actual, dashed = expected), + a table of the days generation deviated most from what temperature predicted. A scenario baseline (explains the past), not a forecast.

**Prices tab**
- **National energy prices, indexed to 100, log scale** — `vw_fuel_prices_daily`, up to 6 FRED series indexed to 100 at the range start: units differ wildly ($/bbl vs $/MMBtu vs PPI points), so relative moves on one honest axis are the comparable signal, and the log y keeps a 12× Henry Hub spike from flattening everything else (equal steps = equal % moves). (Replaced a raw-units multiselect and a dual-axis price↔demand chart — dual axes mislead; price↔demand correlation was measured at r≈0 in levels and stays out until Phase 5's locational prices.)

**Operators tab** (forecast accuracy — Phase 3a's marts)
- **League table** — `vw_demand_accuracy`, mean daily WAPE of each operator's own day-ahead demand forecast over the selected range, best first (sorted bar + full table). Only demand-reporting BAs appear (generation-only BAs publish no D/DF); partial days (`hours_scored < 23`) excluded.
- **Forecast error over time** — the selected BA's daily WAPE (blue, always ≥ 0) and signed bias (red; positive = over-forecast, M-15) on one axis, with a zero rule.
- **Does temperature break the forecast?** — daily WAPE vs `tavg` (`vw_energy_daily`); extreme temperatures are the hard days, so a U-shape is expected and no fit line is drawn.

**Data health tab** — per-source freshness, BA coverage on the latest day, the last fully-complete day, and the exclusions the weather charts apply (partial days + ~3-day weather lag).

Charts are [Altair](https://altair-viz.github.io/) (fit lines, tooltips, dual axes); simple time series stay Streamlit built-ins.
