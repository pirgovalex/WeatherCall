with raw as (
    select
        _audit,
        hourly
    from {{ source('landing', 'cams_aq') }}
),
unnested as (
    select
        _audit.fetched_at_utc::timestamptz as fetched_at_utc,
        date_trunc('hour', unnest(hourly.time)::timestamptz) as valid_at_utc,
        unnest(hourly.pm10) as raw_pm10,
        unnest(hourly.pm2_5) as raw_pm2_5,
        unnest(hourly.nitrogen_dioxide) as raw_no2
    from raw
)
select
    valid_at_utc,
    'plovdiv' as location_id,
    case
        when valid_at_utc <= date_trunc('hour', fetched_at_utc) then 'analysis'
        else 'forecast'
    end as source_type,
    fetched_at_utc,
    
    case when raw_pm2_5 < 0.0 then null else round(raw_pm2_5, 4) end as pm2_5,
    case when raw_pm10 < 0.0 then null else round(raw_pm10, 4) end as pm10,
    case when raw_no2 < 0.0 then null else round(raw_no2, 4) end as no2
from unnested
