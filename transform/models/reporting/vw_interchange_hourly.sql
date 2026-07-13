-- Reporting view over fct_interchange_hourly for the public Streamlit dashboard
-- (the Flows tab's arc map). 1:1 pass-through (no logic) — the governance
-- boundary, not a transform (M-13). Same grain as the mart: one directed row
-- per (period, fromba, toba); both directions of a pair are distinct rows (M-22).
{{ config(materialized='view') }}

select
    period,
    fromba,
    fromba_name,
    toba,
    toba_name,
    flow_mwh
from {{ ref('fct_interchange_hourly') }}
