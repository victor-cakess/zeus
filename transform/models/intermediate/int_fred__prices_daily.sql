-- FRED mixed-frequency price series reshaped onto a daily calendar spine — one row
-- per date, one column per series (wide). Each series is last-observation-carried-
-- forward (LOCF) across the gaps its native frequency leaves (weekends/holidays for
-- daily spots, intra-week for weekly retail, intra-month for monthly indexes), AND
-- across the trailing publication-lag window (M-10). Leading edge (before a series'
-- first observation) stays null. Two companions per series expose missingness as
-- signal (M-11): <series>_is_observed (real obs vs carry-forward) and
-- <series>_staleness_days (days since last real obs; 0 on an observed day, null
-- before the first). Values are latest-revision, NOT point-in-time — as-of
-- reconstruction is deferred (M-11).
--
-- The series list is the single source of the wide column set; it mirrors the SERIES
-- map in src/lambdas/fred/ingest/client.py. A Jinja loop generates the 15 × 3 columns
-- to keep the model DRY (M-10).
{% set series_list = [
    'WTI', 'BRENT', 'HENRYHUB', 'HEATINGOIL', 'PROPANEMT', 'JETFUEL', 'GASNYH', 'GASGULF',
    'GASOLINE', 'DIESEL', 'COALPPI', 'NATGASPPI', 'ELECPPI', 'ELECPRICE', 'CPIENERGY'
] %}

{% set spine_start %}(select min(date) from {{ ref('stg_fred__prices') }}){% endset %}

with prices as (
    select * from {{ ref('stg_fred__prices') }}
),

-- daily calendar spine: earliest observation across all series → today (inclusive).
spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date=spine_start,
        end_date="dateadd(day, 1, current_date)"
    ) }}
),

-- pivot the long staging to wide: one row per date, raw observed value per series
-- (null where that series had no observation that date). One row per (series, date)
-- upstream, so max() just lifts the single value.
observed as (
    select
        date,
        {% for s in series_list -%}
        max(case when series = '{{ s }}' then value end) as {{ s | lower }}{{ "," if not loop.last }}
        {% endfor %}
    from prices
    group by date
),

-- every calendar day, left-joined to whatever was observed that day.
joined as (
    select
        spine.date_day as date,
        {% for s in series_list -%}
        observed.{{ s | lower }}{{ "," if not loop.last }}
        {% endfor %}
    from spine
    left join observed on spine.date_day = observed.date
),

-- LOCF each series forward over the spine; derive the is_observed / staleness_days
-- companions from the raw (pre-fill) value.
filled as (
    select
        date,
        {% for s in series_list -%}
        {%- set c = s | lower %}
        last_value({{ c }} ignore nulls) over (
            order by date rows between unbounded preceding and current row
        ) as {{ c }},
        {{ c }} is not null as {{ c }}_is_observed,
        datediff(
            day,
            last_value(case when {{ c }} is not null then date end) ignore nulls over (
                order by date rows between unbounded preceding and current row
            ),
            date
        ) as {{ c }}_staleness_days{{ "," if not loop.last }}
        {% endfor %}
    from joined
)

select * from filled
