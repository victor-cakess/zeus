-- Daily forecast-accuracy scorecard: how well did a forecaster predict each BA's
-- hourly demand? Grain: one row per (ba, date, forecaster) — LONG (DECISIONS.md
-- M-15): Phase 4 adds forecasters (naive baseline, zeus model) as rows, never as
-- schema changes. Today the only forecaster is the grid operators' own day-ahead
-- demand forecast ('eia_df').
--
-- Only hours where BOTH demand and the forecast are present are scored
-- (hours_scored counts them); BA-days with nothing to score produce no row, so
-- generation-only BAs never appear. Daily error metrics are ratios of the day's
-- sums (M-6 spirit), not means of hourly ratios:
--   wape     = sum(|D - DF|) / sum(|D|)  — primary (M-15)
--   bias_pct = sum(DF - D) / sum(|D|)    — signed; positive = over-forecast
--   mape     = mean over D<>0 hours of |D - DF| / |D| — secondary; near-zero-
--              demand hours inflate it, which is why WAPE is primary (M-15)
-- Plain table rebuilt from the hourly fact each run (M-5/M-16) — it never
-- re-reads landing.
with scored_hours as (
    select
        ba,
        period::date as date,
        demand_mwh,
        demand_forecast_mwh
    from {{ ref('fct_demand_hourly') }}
    where demand_mwh is not null
      and demand_forecast_mwh is not null
)

select
    ba,
    date,
    'eia_df' as forecaster,
    -- null (not divide-by-zero) on the degenerate all-zero-demand day
    sum(abs(demand_mwh - demand_forecast_mwh))
        / nullif(sum(abs(demand_mwh)), 0) as wape,
    sum(demand_forecast_mwh - demand_mwh)
        / nullif(sum(abs(demand_mwh)), 0) as bias_pct,
    avg(
        case
            when demand_mwh <> 0
                then abs(demand_mwh - demand_forecast_mwh) / abs(demand_mwh)
        end
    ) as mape,
    count(*) as hours_scored
from scored_hours
group by ba, date
