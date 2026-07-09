-- The cross-source daily mart: EIA generation rolled up to (ba, date), joined
-- LEFT to NOAA weather and to EIA-region demand. Grain: one row per (ba, date) —
-- row existence tracks generation (DECISIONS.md M-4): all 71 BAs appear; weather
-- columns are null for uncovered BAs and inside NOAA's ~3-day publication lag;
-- demand columns are null for generation-only BAs (M-14 — same nullable-join
-- policy as weather).
--
-- date = UTC calendar day of period (M-7; weather joins its station-local
-- observation_date as-is). renewable_share = ratio of the daily sums, NOT the
-- mean of hourly shares (M-6). Plain table rebuilt from the hourly fact each
-- run (M-5) — it never re-reads landing.
with generation_daily as (
    select
        ba,
        period::date as date,
        sum(total_net_mwh) as total_net_mwh,
        sum(total_gross_mwh) as total_gross_mwh,
        sum(renewable_gross_mwh) as renewable_gross_mwh,
        count(distinct period) as hours_reported
    from {{ ref('fct_generation_hourly') }}
    group by ba, period::date
),

demand_daily as (
    select
        ba,
        period::date as date,
        sum(demand_mwh) as demand_mwh,
        sum(demand_forecast_mwh) as demand_forecast_mwh
    from {{ ref('fct_demand_hourly') }}
    group by ba, period::date
),

weather as (
    select * from {{ ref('int_noaa__weather_daily') }}
)

select
    g.ba,
    g.date,
    g.total_net_mwh,
    g.total_gross_mwh,
    g.renewable_gross_mwh,
    -- null (not divide-by-zero) when the day produced no gross generation
    g.renewable_gross_mwh / nullif(g.total_gross_mwh, 0) as renewable_share,
    g.hours_reported,
    d.demand_mwh,
    d.demand_forecast_mwh,
    w.station_count,
    w.tmax,
    w.tmin,
    w.tavg,
    w.prcp,
    w.snow,
    w.snwd,
    w.awnd,
    w.wsf2,
    w.wsf5,
    w.rhav,
    w.aslp,
    w.adpt
from generation_daily g
left join demand_daily d
    on g.ba = d.ba
    and g.date = d.date
left join weather w
    on g.ba = w.ba
    and g.date = w.observation_date
