-- NOAA daily weather summaries — clean + standardize the landing table.
-- Grain: one row per (station, observation_date); each station maps to one ba.
-- Dedup: the rolling lookback (and NOAA's late/QC-revised days) re-write the same
-- (date, station); keep the row from the latest ingestion_date.
-- Wide datatype columns are passed through as-is; absent datatypes are null by design
-- (no aggregation here — station -> ba rollup happens in the intermediate layer).
with source as (
    select * from {{ source('noaa', 'noaa_grid') }}
),

deduplicated as (
    select
        date as observation_date,          -- calendar day of the summary (station local day)
        station,                           -- NOAA GHCND station id
        ba,                                -- balancing authority; join key to EIA
        tmax,
        tmin,
        tavg,
        prcp,
        snow,
        snwd,
        awnd,
        wsf2,
        wsf5,
        wdf2,
        rhav,
        aslp,
        adpt,
        ingestion_date
    from source
    qualify row_number() over (
        partition by station, date
        order by ingestion_date desc
    ) = 1
)

select * from deduplicated
