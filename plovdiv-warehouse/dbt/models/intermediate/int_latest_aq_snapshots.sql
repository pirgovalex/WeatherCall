select
    valid_at_utc,
    location_id,
    source_type,
    fetched_at_utc,
    pm2_5,
    pm10,
    no2
from {{ ref('stg_cams_aq') }}
qualify row_number() over (
    partition by location_id, valid_at_utc, source_type 
    order by fetched_at_utc desc
) = 1
