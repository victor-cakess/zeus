-- Hourly interchange fact — stg_eia_interchange__flows materialized incrementally
-- so consumers stop recomputing the staging dedup over the full landing table
-- (~23M rows backfilled). Grain: one directed row per (period, fromba, toba) —
-- no intermediate layer: there is no business rule or grain change between
-- staging and this fact, only materialization (M-21). Config (incremental,
-- merge on the grain) lives in _marts__models.yml next to the contract.
--
-- Incremental window (M-5, applied per M-21): each run re-merges the trailing
-- 10 days — a period receives revisions until it ages out of eia_interchange's
-- 7-day ingestion lookback (an 8-calendar-day span), +2 days operational slack.
-- Anchored on max(period) already in the table, so a paused pipeline catches up
-- without a hole. Schema/logic changes need --full-refresh.
with flows as (
    select
        period,
        fromba,
        fromba_name,
        toba,
        toba_name,
        flow_mwh
    from {{ ref('stg_eia_interchange__flows') }}
)

select *
from flows
{% if is_incremental() %}
where period >= dateadd(day, -10, (select max(period) from {{ this }}))
{% endif %}
