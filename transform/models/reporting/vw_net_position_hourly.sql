-- Reporting view over fct_net_position_hourly for the public Streamlit dashboard
-- (the Flows tab's diverging bar + hour-of-day heatmap). 1:1 pass-through (no
-- logic) — the governance boundary, not a transform (M-13). Same grain as the
-- mart: one row per (ba, period); net_position_mwh positive = net exporter (M-22).
{{ config(materialized='view') }}

select
    ba,
    period,
    exports_mwh,
    imports_mwh,
    net_position_mwh
from {{ ref('fct_net_position_hourly') }}
