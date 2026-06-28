-- Reporting view over fct_fuel_prices_daily for the dashboard price charts.
-- 1:1 pass-through — see vw_energy_daily for the governance rationale. select *
-- exposes the full mart (date + 15 series, each with _is_observed / _staleness_days
-- companions, M-11): the dashboard is still evolving, and the missingness/staleness
-- signal is exactly what a public dashboard should be free to surface. National
-- grain (one row per date, no ba).
{{ config(materialized='view') }}

select * from {{ ref('fct_fuel_prices_daily') }}
