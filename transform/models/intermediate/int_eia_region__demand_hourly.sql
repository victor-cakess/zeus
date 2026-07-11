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

bounded as (
    -- QC bounds (DECISIONS.md M-20): null physically-impossible values before the pivot.
    -- D/DF are magnitudes (demand/forecast can't be negative); NG/TI are signed. US48
    -- (Lower-48 national aggregate) runs ~10x a single BA and gets a higher ceiling;
    -- every non-US48 value over 300k MWh/hour is garbage (2^31 sentinels, bad-data runs,
    -- huge negative "demand"). Nulls stay null through the max() pivot.
    select
        ba,
        period,
        metric,
        case
            when metric in ('D', 'DF') and value_mwh < 0 then null
            when abs(value_mwh) > (case when ba = 'US48' then 1000000 else 300000 end) then null
            else value_mwh
        end as value_mwh
    from region
),

pivoted as (
    select
        ba,
        period,
        max(case when metric = 'D' then value_mwh end) as demand_mwh,
        max(case when metric = 'DF' then value_mwh end) as demand_forecast_mwh,
        max(case when metric = 'NG' then value_mwh end) as net_generation_mwh,
        max(case when metric = 'TI' then value_mwh end) as total_interchange_mwh
    from bounded
    group by ba, period
)

select * from pivoted
