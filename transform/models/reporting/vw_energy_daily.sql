-- Reporting view over fct_energy_daily for the public Streamlit dashboard. 1:1
-- pass-through (no logic) — the governance boundary, not a transform: the dashboard
-- role (ZEUS_DEV_DASHBOARD_ROLE) gets SELECT on REPORTING.* only, never on MARTS or
-- the landing schemas. Rebuilt as a view every dbt build via ref(), so it tracks the
-- mart's columns for free. Same grain as the mart (one row per ba/date; weather cols
-- nullable per M-4).
{{ config(materialized='view') }}

select
    ba,
    date,
    total_net_mwh,
    total_gross_mwh,
    renewable_gross_mwh,
    renewable_share,
    hours_reported,
    station_count,
    tmax,
    tmin,
    tavg,
    prcp,
    snow,
    snwd,
    awnd,
    wsf2,
    wsf5,
    rhav,
    aslp,
    adpt
from {{ ref('fct_energy_daily') }}
