select
    valid_at_utc,
    location_id,
    source_type,
    fetched_at_utc,
    temperature_2m,
    wind_gusts_10m,
    precipitation
from {{ ref('stg_openmeteo_forecast') }}
qualify row_number() over (
    partition by location_id, valid_at_utc, source_type 
    order by fetched_at_utc desc
) = 1
