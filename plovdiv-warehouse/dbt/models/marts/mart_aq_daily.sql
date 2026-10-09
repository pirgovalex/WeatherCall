{{ config(materialized='external', location='../data/marts/mart_aq_daily/latest/mart_aq_daily.parquet') }}

with eea as (
    select
        day,
        location_id,
        pollutant,
        value_ug_m3,
        valid_hours,
        result_time,
        is_valid
    from {{ ref('stg_eea_daily') }}
),
deduped as (
    select
        day,
        location_id,
        pollutant,
        value_ug_m3,
        valid_hours,
        result_time,
        is_valid
    from eea
    qualify row_number() over (
        partition by day, location_id, pollutant
        order by case when is_valid then 1 else 0 end desc, result_time desc nulls last
    ) = 1
),
bounds as (
    select 
        location_id,
        pollutant,
        min(day) as min_day,
        max(day) as max_day
    from deduped
    group by 1, 2
),
spine as (
    select
        location_id,
        pollutant,
        unnest(generate_series(min_day, max_day, interval 1 day))::date as day
    from bounds
)
select
    s.day,
    s.location_id,
    s.pollutant,
    case when d.is_valid then round(d.value_ug_m3, 4) else null end as daily_mean,
    coalesce(case when d.is_valid then d.valid_hours else 0.0 end, 0.0)::integer as valid_hours,
    coalesce(case when d.is_valid then d.valid_hours else 0.0 end, 0.0) >= 18 as is_valid_day
from spine s
left join deduped d
    on s.day = d.day
    and s.location_id = d.location_id
    and s.pollutant = d.pollutant
