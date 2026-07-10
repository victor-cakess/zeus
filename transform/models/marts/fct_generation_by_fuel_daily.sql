-- Daily generation-by-fuel fact — int_eia__generation_by_fuel_daily materialized as a
-- plain table (M-5), the consumption surface behind the dashboard's generation-mix chart.
-- Grain: one row per (ba, date, fuel_type) — LONG, so a new EIA-930 fuel code appears as
-- rows, never a schema change (same shape as fct_demand_accuracy, M-15). gross_mwh only
-- (M-19). Rebuilt from the intermediate each run; single-source (EIA), no cross-source join.
with generation_by_fuel as (
    select
        ba,
        date,
        fuel_type,
        fuel_type_name,
        gross_mwh
    from {{ ref('int_eia__generation_by_fuel_daily') }}
)

select * from generation_by_fuel
