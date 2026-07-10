-- EIA generation by fuel type, rolled up to the day: gross MWh per (ba, date, fuel_type).
-- Grain: one row per (ba, date, fuel_type) — recovers the fuel detail stg_eia__generation
-- carries that int_eia__generation_hourly sums away. Feeds the dashboard's daily
-- generation-mix stacked area (→ fct_generation_by_fuel_daily → vw_generation_by_fuel_daily).
--
-- Business rule (DECISIONS.md M-19): gross MWh only — each fuel's generation clamped at 0
-- before summing (M-1/M-2). A stacked mix needs non-negative contributions; net would let
-- storage-charging hours subtract, which can't stack. date = UTC calendar day of period
-- (M-7). All EIA-930 fuel codes carried through, no grouping — the honest full mix. Rows
-- exist only for the (ba, fuel) combos EIA actually reports (natural sparsity, no zero-fill).
with generation as (
    select * from {{ ref('stg_eia__generation') }}
),

aggregated as (
    select
        ba,
        period::date as date,
        fuel_type,
        max(fuel_type_name) as fuel_type_name,   -- functionally dependent on fuel_type
        sum(greatest(generation_mwh, 0)) as gross_mwh
    from generation
    group by ba, period::date, fuel_type
)

select
    ba,
    date,
    fuel_type,
    fuel_type_name,
    gross_mwh
from aggregated
