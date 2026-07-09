-- EIA region data pivoted from (ba, period, metric) to one row per (ba, period):
-- demand, the operator's day-ahead demand forecast, net generation, and total
-- interchange as columns (DECISIONS.md M-14).
--
-- Stays HOURLY on purpose (aggregate late — M-0 spirit); the accuracy mart rolls
-- up to (ba, date). Nulls stay null: generation-only BAs never report D/DF —
-- missingness is signal, not a defect to fill (M-11 spirit).
with region as (
    select * from {{ ref('stg_eia_region__grid') }}
),

pivoted as (
    select
        ba,
        period,
        max(case when metric = 'D' then value_mwh end) as demand_mwh,
        max(case when metric = 'DF' then value_mwh end) as demand_forecast_mwh,
        max(case when metric = 'NG' then value_mwh end) as net_generation_mwh,
        max(case when metric = 'TI' then value_mwh end) as total_interchange_mwh
    from region
    group by ba, period
)

select * from pivoted
