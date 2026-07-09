-- EIA hourly region data (demand D, day-ahead forecast DF, net generation NG,
-- total interchange TI) — clean + standardize the landing table.
-- Grain: one row per (ba, period, metric). Narrow; the D/DF/NG/TI pivot to wide
-- is intermediate-layer work, not staging's.
-- Dedup: the rolling 7-day lookback writes the same (period, respondent, type)
-- on multiple runs; keep the row from the latest ingestion_date.
with source as (
    select * from {{ source('eia_region', 'eia_region_grid') }}
),

deduplicated as (
    select
        period,                            -- hour, UTC (timestamp_ntz)
        respondent      as ba,             -- balancing authority; join key to EIA/NOAA
        respondent_name as ba_name,
        type            as metric,         -- D / DF / NG / TI
        type_name       as metric_name,
        value           as value_mwh,
        value_units,
        ingestion_date
    from source
    qualify row_number() over (
        partition by period, respondent, type
        order by ingestion_date desc
    ) = 1
)

select * from deduplicated
