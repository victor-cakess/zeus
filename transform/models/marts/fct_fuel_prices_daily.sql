-- The national energy-price mart: int_fred__prices_daily materialized as a plain
-- table for the consumption layer. Grain: one row per date (national — no ba).
-- Standalone, NOT joined into fct_energy_daily — FRED prices join grid/weather on
-- date downstream (M-12). Full rebuild each run (M-5/M-12): PPI/CPI revisions rewrite
-- months-old rows and the LOCF output shifts daily, so incremental buys nothing on a
-- ~4,500-row table. Each <series> carries _is_observed + _staleness_days companions
-- (M-11); values are latest-revision, not point-in-time.
{{ config(materialized='table') }}

select * from {{ ref('int_fred__prices_daily') }}
