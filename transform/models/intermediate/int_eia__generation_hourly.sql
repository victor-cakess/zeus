-- EIA generation reshaped from (ba, period, fuel_type) to one row per (ba, period):
-- net and gross totals, renewable generation, and renewable share.
--
-- Stays HOURLY on purpose (aggregate late — preserves the duck-curve / intraday shape;
-- the daily cross-source mart rolls this up to (ba, date)). See DECISIONS.md M-0.
--
-- Business rules (DECISIONS.md M-1 / M-2):
--  - Negative generation is real (pumped storage, battery charging, solar station
--    service at night), so the model carries BOTH totals: total_net_mwh (honest sum —
--    the demand proxy) and total_gross_mwh (negatives clamped to 0 — production only).
--  - renewable_share is gross-over-gross, so it is in [0, 1] by construction (tested).
--  - Renewables = solar (SUN) + wind (WND) + hydro (WAT). To adjust, edit the IN list.
--    Confirm the fuel codes that actually appear with:
--    select distinct fuel_type from {{ ref('stg_eia__generation') }};
with generation as (
    -- QC bounds (DECISIONS.md M-20): null physically-impossible values before any SUM.
    -- Generation is signed (storage charging / station service are real negatives), so
    -- bound the MAGNITUDE. US48 (Lower-48 national aggregate) runs ~10x a single BA, so
    -- it gets a higher ceiling; every non-US48 value over 300k MWh/fuel/hour is garbage
    -- (sentinels, bad-data runs). Nulls fall out of the sums (greatest(null,0)=null too).
    select
        * exclude (generation_mwh),
        case
            when abs(generation_mwh) > (case when ba = 'US48' then 1000000 else 300000 end)
            then null else generation_mwh
        end as generation_mwh
    from {{ ref('stg_eia__generation') }}
),

aggregated as (
    select
        ba,
        period,
        sum(generation_mwh) as total_net_mwh,
        sum(greatest(generation_mwh, 0)) as total_gross_mwh,
        sum(case when fuel_type in ('SUN', 'WND', 'WAT') then greatest(generation_mwh, 0) else 0 end) as renewable_gross_mwh
    from generation
    group by ba, period
)

select
    ba,
    period,
    total_net_mwh,
    total_gross_mwh,
    renewable_gross_mwh,
    -- null (not divide-by-zero) when a BA-hour produced no gross generation
    renewable_gross_mwh / nullif(total_gross_mwh, 0) as renewable_share
from aggregated
