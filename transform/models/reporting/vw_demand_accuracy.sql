-- Reporting view over fct_demand_accuracy for the public Streamlit dashboard
-- (Operators tab). 1:1 pass-through (no logic) — the governance boundary, not a
-- transform (M-13): the dashboard role gets SELECT on REPORTING.* only. Same
-- grain as the mart: one row per (ba, date, forecaster) — long, so Phase 4's
-- forecasters appear here as rows with no view change (M-15).
{{ config(materialized='view') }}

select
    ba,
    date,
    forecaster,
    wape,
    bias_pct,
    mape,
    hours_scored
from {{ ref('fct_demand_accuracy') }}
