{{ config(materialized='external', location='../data/marts/mart_weather_aq_hourly/latest/mart_weather_aq_hourly.parquet') }}

select
    coalesce(w.valid_at_utc, a.valid_at_utc) as valid_at_utc,
    coalesce(w.location_id, a.location_id) as location_id,
    coalesce(w.source_type, a.source_type) as source_type,
    a.pm2_5,
    a.pm10,
    a.no2,
    w.temperature_2m,
    w.wind_gusts_10m,
    w.precipitation
from {{ ref('int_latest_weather_snapshots') }} w
full outer join {{ ref('int_latest_aq_snapshots') }} a
    on w.valid_at_utc = a.valid_at_utc
    and w.location_id = a.location_id
    and w.source_type = a.source_type
