-- EIA generation reshaped from (ba, period, fuel_type) to one row per (ba, period):
-- total generation, renewable generation, and renewable share.
--
-- Stays HOURLY on purpose (aggregate late — preserves the duck-curve / intraday shape;
-- the daily cross-source mart rolls this up to (ba, date)).
--
-- Business rule: renewables = solar (SUN) + wind (WND) + hydro (WAT), per the roadmap's
-- renewable-share definition. To adjust, edit the IN list. Confirm the fuel codes that
-- actually appear with:  select distinct fuel_type from {{ ref('stg_eia__generation') }};
with generation as (
    select * from {{ ref('stg_eia__generation') }}
),

aggregated as (
    select
        ba,
        period,
        sum(generation_mwh) as total_mwh,
        sum(case when fuel_type in ('SUN', 'WND', 'WAT') then generation_mwh else 0 end) as renewable_mwh
    from generation
    group by ba, period
)

select
    ba,
    period,
    total_mwh,
    renewable_mwh,
    -- null (not divide-by-zero) when a BA-hour reported no/zero total generation
    renewable_mwh / nullif(total_mwh, 0) as renewable_share
from aggregated
