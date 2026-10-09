with raw as (
    select
        _audit,
        hourly,
        hourly_units
    from {{ source('landing', 'openmeteo_forecast') }}
),
unnested as (
    select
        _audit.fetched_at_utc::timestamptz as fetched_at_utc,
        date_trunc('hour', unnest(hourly.time)::timestamptz) as valid_at_utc,
        unnest(hourly.temperature_2m) as raw_temp,
        unnest(hourly.wind_gusts_10m) as raw_wind,
        unnest(hourly.precipitation) as raw_precip,
        hourly_units.temperature_2m as unit_temp,
        hourly_units.wind_gusts_10m as unit_wind,
        hourly_units.precipitation as unit_precip
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
    
    case 
        when raw_temp < -60.0 or raw_temp > 60.0 then null
        else round(raw_temp, 4)
    end as temperature_2m,
    
    case
        when raw_wind < 0.0 then null
        else round(
            case 
                when unit_wind = 'km/h' then raw_wind / 3.6 
                else raw_wind 
            end, 4
        )
    end as wind_gusts_10m,
    
    case
        when raw_precip < 0.0 then null
        else round(raw_precip, 4)
    end as precipitation
from unnested
