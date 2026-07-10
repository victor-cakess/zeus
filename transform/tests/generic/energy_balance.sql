-- Cross-metric physics test (DECISIONS.md M-18): demand should equal net
-- generation minus total interchange (EIA sign convention: TI positive = net
-- exports). Checked at (unit, day) grain — daily sums absorb the hourly
-- metering-clock noise between the three independently reported series — and a
-- breach must clear BOTH tolerances: relative (vs the day's demand) so big BAs
-- aren't flagged for proportionally tiny gaps, and absolute so small BAs aren't
-- flagged for trivial MWh. Run with severity warn: breaches are real
-- reporting-quality findings to surface, not rows to clean (M-8 policy).
{% test energy_balance(model, demand_col, net_generation_col, total_interchange_col,
                       unit_col='ba', period_col='period',
                       rel_tolerance=0.05, abs_tolerance_mwh=500) %}

with daily as (

    select
        {{ unit_col }} as unit,
        {{ period_col }}::date as date,
        sum({{ demand_col }}) as demand_mwh,
        sum({{ net_generation_col }}) - sum({{ total_interchange_col }}) as computed_demand_mwh,
        sum(abs({{ demand_col }})) as abs_demand_mwh
    from {{ model }}
    where {{ demand_col }} is not null
      and {{ net_generation_col }} is not null
      and {{ total_interchange_col }} is not null
    group by 1, 2
    having sum(abs({{ demand_col }})) > 0

)

-- nullif on the denominator (not just the HAVING): Snowflake does not
-- guarantee predicate evaluation order across the CTE boundary, so a bare
-- division can still hit the zero-demand rows HAVING is meant to exclude
select
    unit,
    date,
    demand_mwh,
    computed_demand_mwh,
    abs(demand_mwh - computed_demand_mwh) as abs_residual_mwh,
    abs(demand_mwh - computed_demand_mwh) / nullif(abs_demand_mwh, 0) as rel_residual
from daily
where abs(demand_mwh - computed_demand_mwh) > {{ abs_tolerance_mwh }}
  and abs(demand_mwh - computed_demand_mwh) / nullif(abs_demand_mwh, 0) > {{ rel_tolerance }}

{% endtest %}
