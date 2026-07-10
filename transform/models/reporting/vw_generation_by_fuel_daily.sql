-- Reporting view over fct_generation_by_fuel_daily for the dashboard generation-mix
-- stacked area. 1:1 pass-through — the governance boundary, not a transform (see
-- vw_energy_daily for the rationale). One row per (ba, date, fuel_type); gross MWh (M-19).
-- Authorized by the dashboard role's SELECT-on-future-REPORTING-views grant, so a new
-- vw_* needs no Terraform apply.
{{ config(materialized='view') }}

select
    ba,
    date,
    fuel_type,
    fuel_type_name,
    gross_mwh
from {{ ref('fct_generation_by_fuel_daily') }}
