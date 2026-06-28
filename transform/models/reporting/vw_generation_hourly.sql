-- Reporting view over fct_generation_hourly for the dashboard duck-curve chart.
-- 1:1 pass-through — see vw_energy_daily for the governance rationale. One row per
-- (ba, period); period is UTC (M-7).
{{ config(materialized='view') }}

select
    ba,
    period,
    total_net_mwh,
    total_gross_mwh,
    renewable_gross_mwh,
    renewable_share
from {{ ref('fct_generation_hourly') }}
