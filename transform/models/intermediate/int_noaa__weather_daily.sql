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

bounded as (
    -- QC bounds (DECISIONS.md M-20): null out-of-range sensor readings (e.g. the known
    -- -72.7 °C tmin, 152 m/s wind) before the areal mean, using the SAME physical ranges
    -- the staging schema.yml asserts (M-8). tavg is derived from tmax/tmin below and wdf2
    -- is dropped in aggregation, so neither needs bounding here. Caveat: station_count
    -- still counts a station present in the row even when a given datatype was nulled,
    -- so it can slightly overstate the backing for a specific datatype.
    select
        ba,
        observation_date,
        station,
        case when tmax between -60 and 60   then tmax end as tmax,
        case when tmin between -60 and 60   then tmin end as tmin,
        case when prcp between 0 and 1000   then prcp end as prcp,
        case when snow between 0 and 2500   then snow end as snow,
        case when snwd between 0 and 12000  then snwd end as snwd,
        case when awnd between 0 and 50     then awnd end as awnd,
        case when wsf2 between 0 and 100    then wsf2 end as wsf2,
        case when wsf5 between 0 and 120    then wsf5 end as wsf5,
        case when rhav between 0 and 100    then rhav end as rhav,
        case when aslp between 870 and 1090 then aslp end as aslp,
        case when adpt between -60 and 40   then adpt end as adpt
    from weather
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
    from bounded
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
