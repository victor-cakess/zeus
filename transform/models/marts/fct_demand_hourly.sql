-- Hourly demand fact — int_eia_region__demand_hourly materialized incrementally
-- so consumers stop recomputing the staging dedup + pivot over the full landing
-- table (17M+ rows). Grain: one row per (ba, period). Config (incremental,
-- merge on the grain) lives in _marts__models.yml next to the contract.
--
-- Incremental window (DECISIONS.md M-5, applied per M-16): each run re-merges
-- the trailing 10 days — a period receives revisions until it ages out of
-- eia_region's 7-day ingestion lookback (an 8-calendar-day span), +2 days
-- operational slack. Anchored on max(period) already in the table, so a paused
-- pipeline catches up without a hole. Schema/logic changes need --full-refresh.
with demand as (
    select
        ba,
        period,
        demand_mwh,
        demand_forecast_mwh,
        net_generation_mwh,
        total_interchange_mwh
    from {{ ref('int_eia_region__demand_hourly') }}
)

select *
from demand
{% if is_incremental() %}
where period >= dateadd(day, -10, (select max(period) from {{ this }}))
{% endif %}
