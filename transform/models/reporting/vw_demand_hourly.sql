-- Reporting view over fct_demand_hourly for the public Streamlit dashboard.
-- 1:1 pass-through (no logic) — the governance boundary, not a transform
-- (M-13). Same grain as the mart: one row per (ba, period); D/DF nullable for
-- generation-only BAs (M-14).
{{ config(materialized='view') }}

select
    ba,
    period,
    demand_mwh,
    demand_forecast_mwh,
    net_generation_mwh,
    total_interchange_mwh
from {{ ref('fct_demand_hourly') }}
