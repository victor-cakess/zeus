-- EIA hourly generation by fuel type — clean + standardize the landing table.
-- Grain: one row per (ba, period, fuel_type).
-- Dedup: the rolling 7-day lookback writes the same (period, respondent, fueltype)
-- on multiple runs; keep the row from the latest ingestion_date.
with source as (
    select * from {{ source('eia', 'eia_grid') }}
),

deduplicated as (
    select
        period,                            -- hour of generation (UTC, timestamp_ntz)
        respondent      as ba,             -- balancing authority; join key to NOAA
        respondent_name as ba_name,
        fueltype        as fuel_type,
        type_name       as fuel_type_name,
        value           as generation_mwh,
        value_units,
        ingestion_date
    from source
    qualify row_number() over (
        partition by period, respondent, fueltype
        order by ingestion_date desc
    ) = 1
)

select * from deduplicated
