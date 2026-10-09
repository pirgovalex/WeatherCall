with raw as (
    select
        Samplingpoint,
        Pollutant,
        "Start",
        Validity,
        Unit,
        "Value",
        DataCapture,
        ResultTime
    from {{ source('landing', 'eea_daily') }}
),
extracted as (
    select
        regexp_extract(Samplingpoint, 'SPO-([A-Z0-9]+)_', 1) as station_id,
        
        case 
            when Samplingpoint like '%BG0051A%' then 'plovdiv'
            when Samplingpoint like '%BG0078A%' then 'plovdiv-trakia'
        end as location_id,
        
        case try_cast(Pollutant as varchar)
            when '5' then 'pm10'
            when '6001' then 'pm2_5'
            when '8' then 'no2'
        end as pollutant,
        
        date_trunc('day', "Start")::date as "day",
        
        try_cast(Validity as integer) as validity,
        
        case 
            when Unit in ('ug.m-3', 'ug/m3', 'μg/m³', 'µg/m³') then try_cast("Value" as double)
            when Unit = 'mg.m-3' then try_cast("Value" as double) * 1000.0
            when Unit = 'ng.m-3' then try_cast("Value" as double) * 0.001
            else try_cast("Value" as double)
        end as value_ug_m3,
        
        case
            when DataCapture is null then 18.0
            else (
                case 
                    when try_cast(DataCapture as double) > 1.0 then try_cast(DataCapture as double) / 100.0
                    else try_cast(DataCapture as double)
                end
            ) * 24.0
        end as valid_hours,
        
        try_cast(ResultTime as timestamp) as result_time
    from raw
)
select
    station_id,
    location_id,
    pollutant,
    "day",
    validity,
    value_ug_m3,
    valid_hours,
    result_time,
    (validity = 1 and value_ug_m3 is not null and not isinf(value_ug_m3) and value_ug_m3 >= 0) as is_valid
from extracted
