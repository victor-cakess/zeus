-- Hourly generation fact — int_eia__generation_hourly materialized incrementally
-- so consumers stop recomputing the staging dedup over the full landing table.
-- Grain: one row per (ba, period). Config (incremental, merge on the grain) lives
-- in _marts__models.yml next to the contract.
--
-- Incremental window (DECISIONS.md M-5): each run re-merges the trailing 10 days —
-- a period receives revisions until it ages out of the 7-day ingestion lookback
-- (an 8-calendar-day span), +2 days operational slack. Anchored on max(period)
-- already in the table, so a paused pipeline catches up without a hole.
-- Schema/logic changes need --full-refresh.
with generation as (
    select
        ba,
        period,
        total_net_mwh,
        total_gross_mwh,
        renewable_gross_mwh,
        renewable_share
    from {{ ref('int_eia__generation_hourly') }}
)

select *
from generation
{% if is_incremental() %}
where period >= dateadd(day, -10, (select max(period) from {{ this }}))
{% endif %}
