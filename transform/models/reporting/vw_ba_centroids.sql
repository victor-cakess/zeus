-- Reporting view over the ba_centroids seed for the public Streamlit dashboard
-- (the Flows tab joins it in pandas against both ends of each arc, M-23).
-- 1:1 pass-through (no logic) — the governance boundary, not a transform (M-13);
-- the seed itself lives in SEEDS, which the dashboard role can't read.
{{ config(materialized='view') }}

select
    ba,
    ba_name,
    latitude,
    longitude
from {{ ref('ba_centroids') }}
