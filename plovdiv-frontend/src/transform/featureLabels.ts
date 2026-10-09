/**
 * Plain-English names for the features of the real PM10 risk model (src/plovdiv/ml/features.py in the
 * warehouse). The API returns the raw column names.
 *
 * Day t is the as-of day, normally today; a name ending in `_t1` describes day t + 1, tomorrow, the
 * day the score is for. The calendar features describe the target day. A feature whose name starts
 * with `log_` is the natural logarithm of a concentration in the model; the label names the
 * concentration, since the logarithm is a modelling detail.
 */
export const MODEL_FEATURE_LABELS: Record<string, string> = {
  // PM10 and PM2.5 up to and including day t (daily means)
  log_pm10_t: 'PM10 today',
  log_pm10_t_minus_1: 'PM10 yesterday',
  log_pm10_mean3: 'Average PM10, last 3 days',
  log_pm10_mean7: 'Average PM10, last 7 days',
  log_pm25_t: 'PM2.5 today',

  // Calendar attributes of the target day (tomorrow)
  month_sin: 'Time of year (sine)',
  month_cos: 'Time of year (cosine)',
  is_weekend: 'Tomorrow is a weekend day',
  heating_season: 'Heating season (October to March)',

  // Weather of day t (today)
  temp_mean_c_t: 'Average temperature today',
  temp_range_c_t: 'Temperature range today',
  log1p_precip_mm_t: 'Total precipitation today',
  wind_max_ms_t: 'Strongest wind today',
  radiation_mj_m2_t: 'Solar radiation today',
  rh_mean_pct_t: 'Average humidity today',
  pressure_mean_hpa_t: 'Average air pressure today',

  // Weather of day t + 1 (tomorrow)
  temp_mean_c_t1: 'Average temperature tomorrow',
  temp_range_c_t1: 'Temperature range tomorrow',
  log1p_precip_mm_t1: 'Total precipitation tomorrow',
  wind_max_ms_t1: 'Strongest wind tomorrow',
  radiation_mj_m2_t1: 'Solar radiation tomorrow',
  rh_mean_pct_t1: 'Average humidity tomorrow',
  pressure_mean_hpa_t1: 'Average air pressure tomorrow',
}
