-- FRED national energy price series — clean + standardize the landing table.
-- Grain: one row per (series, date) — one observed value per price series per day.
-- Dedup: the 150-day lookback (and the backfill/daily overlap) re-write the same
-- (series, date); keep the row from the latest ingestion_date — which is also the
-- latest BLS revision for the monthly PPI/CPI series. Values pass through in native
-- units (no scaling); the per-group bounds tests live in the schema.yml (M-9).
with source as (
    select * from {{ source('fred', 'fred_grid') }}
),

deduplicated as (
    select
        series,                            -- unit slug (WTI, COALPPI, …); fan-out + grain key
        series_id,                         -- FRED series id (DCOILWTICO, …)
        date,                              -- observation date
        value,                             -- observed value, native units (see schema.yml)
        ingestion_date
    from source
    qualify row_number() over (
        partition by series, date
        order by ingestion_date desc
    ) = 1
)

select * from deduplicated
