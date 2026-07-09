-- NOAA weather aggregated from station grain to one row per (ba, observation_date) —
-- the grain that joins to EIA generation.
--
-- Aggregation rule: areal MEAN across the BA's stations for every datatype (avg, not
-- sum — each value is a single station's reading, so the BA-representative value is the
-- average, including for precipitation and snow). WDF2 (wind *direction*, degrees) is
-- deliberately dropped: direction is a circular quantity, so a plain average is wrong
-- (avg of 350 and 10 is 180, the opposite of the true ~0). station_count exposes how
-- many stations backed each BA-day — a data-quality signal for downstream.
with weather as (
    select * from {{ ref('stg_noaa__weather') }}
),

aggregated as (
    select
        ba,
        observation_date,
        count(distinct station) as station_count,
        avg(tmax) as tmax,
        avg(tmin) as tmin,
        -- derived, NOT the raw TAVG datatype (99.9% null in our station set) —
        -- DECISIONS.md M-3; null when either input mean is null (honest nulls)
        (avg(tmax) + avg(tmin)) / 2 as tavg,
        avg(prcp) as prcp,
        avg(snow) as snow,
        avg(snwd) as snwd,
        avg(awnd) as awnd,
        avg(wsf2) as wsf2,
        avg(wsf5) as wsf5,
        avg(rhav) as rhav,
        avg(aslp) as aslp,
        avg(adpt) as adpt
    from weather
    group by ba, observation_date
)

select
    *,
    -- degree days, base 65°F over the derived tavg (DECISIONS.md M-17): US
    -- industry convention is °F-days, our temps are °C, so convert inline.
    -- greatest(null, 0) is null in Snowflake, so days without tavg stay
    -- honestly null (M-4 policy downstream).
    greatest(65 - (tavg * 9 / 5 + 32), 0) as hdd,
    greatest((tavg * 9 / 5 + 32) - 65, 0) as cdd
from aggregated
