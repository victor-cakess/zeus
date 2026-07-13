-- Cross-report physics test (DECISIONS.md M-22): the two directions of a BA pair
-- report the same physical flow with opposite signs, so their signed sum should
-- be ~0. Checked at (pair, day) grain via canonical ordering — least/greatest
-- collapses both directions into one group, one scan, no self-join, and each
-- violation reports once. Pairs where only one side reported are excluded (the
-- HAVING): a counterparty outside the fan-out list trivially "breaches" with a
-- residual equal to the whole flow — that is coverage, not asymmetry. A breach
-- must clear BOTH tolerances: relative (vs the pair's gross flow) so big
-- interties aren't flagged for proportionally tiny gaps, and absolute so small
-- ones aren't flagged for trivial MWh. Run with severity warn: breaches are
-- real reporting-quality findings to surface, not rows to clean (M-8 policy).
{% test interchange_asymmetry(model, flow_col='flow_mwh', from_col='fromba', to_col='toba',
                              period_col='period',
                              rel_tolerance=0.05, abs_tolerance_mwh=500) %}

with daily_pairs as (

    select
        {{ period_col }}::date as date,
        least({{ from_col }}, {{ to_col }}) as ba_a,
        greatest({{ from_col }}, {{ to_col }}) as ba_b,
        sum({{ flow_col }}) as residual_mwh,
        sum(abs({{ flow_col }})) as gross_flow_mwh
    from {{ model }}
    where {{ flow_col }} is not null
    group by 1, 2, 3
    having count(distinct {{ from_col }}) = 2
       and sum(abs({{ flow_col }})) > 0

)

-- nullif on the denominator (not just the HAVING): Snowflake does not
-- guarantee predicate evaluation order across the CTE boundary, so a bare
-- division can still hit the zero-flow rows HAVING is meant to exclude
select
    date,
    ba_a,
    ba_b,
    residual_mwh,
    gross_flow_mwh,
    abs(residual_mwh) as abs_residual_mwh,
    abs(residual_mwh) / nullif(gross_flow_mwh, 0) as rel_residual
from daily_pairs
where abs(residual_mwh) > {{ abs_tolerance_mwh }}
  and abs(residual_mwh) / nullif(gross_flow_mwh, 0) > {{ rel_tolerance }}

{% endtest %}
