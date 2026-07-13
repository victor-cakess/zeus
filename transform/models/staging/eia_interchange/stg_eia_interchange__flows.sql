-- EIA hourly interchange data (BA-to-BA power flows) — clean + standardize the
-- landing table. Grain: one row per (period, fromba, toba), directed: both
-- directions of a pair are distinct rows, each reported by its own fromba
-- (deliberate — the interchange_asymmetry test compares them, M-22).
-- Dedup: the rolling 7-day lookback writes the same (period, fromba, toba) on
-- multiple runs; keep the row from the latest ingestion_date.
with source as (
    select * from {{ source('eia_interchange', 'eia_interchange_grid') }}
),

deduplicated as (
    select
        period,                 -- hour, UTC (timestamp_ntz)
        fromba,                 -- reporting balancing authority; same code set as stg_eia__generation
        fromba_name,
        toba,                   -- neighboring balancing authority (incl. Canadian/Mexican counterparties)
        toba_name,
        value as flow_mwh,      -- signed: positive = fromba exports to toba
        value_units,
        ingestion_date
    from source
    qualify row_number() over (
        partition by period, fromba, toba
        order by ingestion_date desc
    ) = 1
)

select * from deduplicated
