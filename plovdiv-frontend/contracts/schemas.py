"""Pandera DataFrame schemas for Plovdiv weather and air quality warehouse data marts.

Marts:
1. mart_weather_aq_hourly: grain (location_id, valid_at_utc, source_type)
2. mart_aq_daily: grain (day, location_id, pollutant)
3. mart_aq_trend_monthly: grain (station_id, pollutant, month); optional
4. mart_aq_risk_daily: grain (station_id, target_date, model_id); optional
5. mart_aq_risk_drivers: grain (station_id, target_date, model_id, feature); optional
6. mart_model_card: grain (model_id); optional
7. mart_model_calibration: grain (model_id, bin_lo); optional

Marts 3 to 7 are optional in manifest.json. Station ids are data, not part of the schema; they are
declared in the manifest stations catalogue.
"""

import datetime
import os

os.environ.setdefault("DISABLE_PANDERA_IMPORT_WARNING", "True")

import numpy as np
import pandas as pd
import pandera as pa
from pandera import Check, Column


def check_datetime_utc(s: pd.Series) -> bool:
    """Assert series contains UTC timestamps."""
    if not pd.api.types.is_datetime64_any_dtype(s):
        return False
    return s.dt.tz is not None and str(s.dt.tz).upper() in ["UTC", "TZUTC()", "DATETIME.TIMEZONE.UTC"]


def check_hour_aligned(s: pd.Series) -> pd.Series:
    """Assert timestamps are strictly aligned to the hour (minute=0, second=0, microsecond=0)."""
    return (s.dt.minute == 0) & (s.dt.second == 0) & (s.dt.microsecond == 0)


def check_is_date(s: pd.Series) -> bool:
    """Assert series contains dates or datetimes (EEA day convention UTC+1)."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    return all(isinstance(x, (datetime.date, datetime.datetime)) for x in s)


def check_18_of_24_rule(df: pd.DataFrame) -> pd.Series:
    """Assert is_valid_day strictly matches the EEA 18-of-24 regulatory completeness rule."""
    expected_valid = df["valid_hours"] >= 18
    return df["is_valid_day"] == expected_valid


VALID_SOURCE_TYPES = [
    "station",
    "analysis",
    "reanalysis_provisional",
    "reanalysis_final",
    "forecast",
]

VALID_POLLUTANTS = [
    "pm2_5",
    "pm10",
    "no2",
]

mart_weather_aq_hourly_schema = pa.DataFrameSchema(
    columns={
        "valid_at_utc": Column(
            checks=[
                Check(check_datetime_utc, error="valid_at_utc must be a UTC timestamp"),
                Check(
                    check_hour_aligned,
                    error="valid_at_utc must be hour-aligned (minute=0, second=0, microsecond=0)",
                ),
            ],
            nullable=False,
            description="UTC timestamp, hour-aligned",
        ),
        "location_id": Column(
            pa.String,
            nullable=False,
            checks=[Check.str_length(min_value=1)],
            description="Location identifier",
        ),
        "source_type": Column(
            pa.String,
            nullable=False,
            checks=[Check.isin(VALID_SOURCE_TYPES)],
            description="Provenance category",
        ),
        "pm2_5": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Fine particulate matter in ug/m3",
        ),
        "pm10": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Particulate matter in ug/m3",
        ),
        "no2": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Nitrogen dioxide in ug/m3",
        ),
        "temperature_2m": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.in_range(-60.0, 60.0)],
            description="Air temperature at 2m in degC",
        ),
        "wind_gusts_10m": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Peak wind gusts at 10m in m/s",
        ),
        "precipitation": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Precipitation in mm",
        ),
    },
    unique=["valid_at_utc", "location_id", "source_type"],
    strict=True,
)

mart_aq_daily_schema = pa.DataFrameSchema(
    columns={
        "day": Column(
            checks=[
                Check(check_is_date, error="day must be a date or datetime (EEA UTC+1)")
            ],
            nullable=False,
            description="Calendar date under EEA convention (UTC+1)",
        ),
        "location_id": Column(
            pa.String,
            nullable=False,
            checks=[Check.str_length(min_value=1)],
            description="Location identifier",
        ),
        "pollutant": Column(
            pa.String,
            nullable=False,
            checks=[Check.isin(VALID_POLLUTANTS)],
            description="Pollutant identifier",
        ),
        "daily_mean": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Arithmetic mean of valid hourly pollutant readings",
        ),
        "valid_hours": Column(
            checks=[
                Check(
                    lambda s: pd.api.types.is_integer_dtype(s),
                    error="valid_hours must be an integer",
                ),
                Check.in_range(0, 24),
            ],
            nullable=False,
            description="Count of valid hourly readings (0 to 24)",
        ),
        "is_valid_day": Column(
            pa.Bool,
            nullable=False,
            description="EEA data-capture compliance flag (true when valid_hours >= 18)",
        ),
    },
    checks=[
        Check(check_18_of_24_rule, error="is_valid_day must strictly follow 18-of-24 rule")
    ],
    unique=["day", "location_id", "pollutant"],
    strict=True,
)


# ---------------------------------------------------------------------------
# Optional marts: air-quality trend, exceedance risk, model card and calibration
# ---------------------------------------------------------------------------

VALID_SPLITS = [
    "train",
    "validation",
    "test",
    "live",
    # Out-of-sample days after the test period, scored after the fact with the inputs a live run
    # would have had (station data as published, forecast-model weather). Not issued in real time.
    "replay",
]


def _dates(s: pd.Series) -> pd.Series:
    """Convert a series of dates or datetimes to naive datetime64 for comparisons."""
    return pd.to_datetime(s)


def check_first_of_month(s: pd.Series) -> pd.Series:
    """Assert every value is the first day of a month."""
    return _dates(s).dt.day == 1


def check_finite(s: pd.Series) -> pd.Series:
    """Assert values are finite numbers (no NaN, no infinity)."""
    return pd.Series(np.isfinite(s.astype("float64")), index=s.index)


def check_trend_consistency(df: pd.DataFrame) -> pd.Series:
    """Row rules for mart_aq_trend_monthly.

    - n_days cannot exceed the number of days in the month.
    - observed_mean_ugm3 is NULL exactly when n_days is 0.
    - The adjusted mean and its bounds are NULL together, or all present with lo <= mean <= hi.
    - A month with no valid days has no adjusted mean.
    """
    days_in_month = _dates(df["month"]).dt.days_in_month
    observed = df["observed_mean_ugm3"]
    adjusted = df["weather_adjusted_mean_ugm3"]
    lo = df["adjusted_lo_ugm3"]
    hi = df["adjusted_hi_ugm3"]

    within_month = df["n_days"] <= days_in_month
    observed_rule = (df["n_days"] == 0) == observed.isna()
    same_null = (adjusted.isna() == lo.isna()) & (adjusted.isna() == hi.isna())
    ordered = ((lo <= adjusted) & (adjusted <= hi)) | adjusted.isna()
    empty_month = (df["n_days"] > 0) | adjusted.isna()
    return within_month & observed_rule & same_null & ordered & empty_month


def check_issued_before_target_day_ends(df: pd.DataFrame) -> pd.Series:
    """Assert a probability is issued before the target EEA day (UTC+1) has ended.

    The EEA day D ends at D 23:00 UTC. A value issued after that is not a prediction.
    """
    day_end_utc = _dates(df["target_date"]).dt.tz_localize("UTC") + pd.Timedelta(hours=23)
    return df["issued_at"] < day_end_utc


def check_drivers_ranking(df: pd.DataFrame) -> bool:
    """Assert ranks run 1..k per prediction and follow the absolute contribution, largest first."""
    for _, group in df.groupby(["station_id", "target_date", "model_id"]):
        ordered = group.sort_values("rank")
        if list(ordered["rank"]) != list(range(1, len(ordered) + 1)):
            return False
        magnitudes = ordered["contribution"].abs().to_numpy()
        if np.any(np.diff(magnitudes) > 1e-12):
            return False
    return True


def check_string_list(s: pd.Series) -> bool:
    """Assert every value is a non-empty list of non-empty, distinct strings."""
    for value in s:
        if value is None or isinstance(value, str):
            return False
        items = list(value)
        if not items or len(set(items)) != len(items):
            return False
        if not all(isinstance(x, str) and len(x) > 0 for x in items):
            return False
    return True


def check_model_windows(df: pd.DataFrame) -> pd.Series:
    """Assert the training, validation and test windows are ordered and do not overlap.

    The model must also have been trained after the training window ended. The training date itself may fall
    after the test window: it records when the fitting ran, not which rows it saw (the window ordering above
    guarantees the test rows are later than the training rows).
    """
    train_start, train_end = _dates(df["train_start"]), _dates(df["train_end"])
    validation_start, validation_end = _dates(df["validation_start"]), _dates(df["validation_end"])
    test_start, test_end = _dates(df["test_start"]), _dates(df["test_end"])
    trained_at = df["trained_at"]
    return (
        (train_start <= train_end)
        & (train_end < validation_start)
        & (validation_start <= validation_end)
        & (validation_end < test_start)
        & (test_start <= test_end)
        & (trained_at >= train_end.dt.tz_localize("UTC"))
    )


def check_calibration_bins(df: pd.DataFrame) -> bool:
    """Assert bins of each model are contiguous and cover 0 to 1."""
    for _, group in df.groupby("model_id"):
        ordered = group.sort_values("bin_lo")
        lo = ordered["bin_lo"].to_numpy()
        hi = ordered["bin_hi"].to_numpy()
        if not (np.isclose(lo[0], 0.0) and np.isclose(hi[-1], 1.0)):
            return False
        if len(lo) > 1 and not np.allclose(lo[1:], hi[:-1]):
            return False
    return True


def check_calibration_rows(df: pd.DataFrame) -> pd.Series:
    """Row rules for mart_model_calibration.

    - bin_lo < bin_hi.
    - predicted_mean and observed_rate are NULL exactly when n is 0.
    - A non-empty bin's predicted_mean lies inside the bin (the last bin includes its upper edge).
    """
    empty = df["n"] == 0
    both_null = df["predicted_mean"].isna() & df["observed_rate"].isna()
    both_present = df["predicted_mean"].notna() & df["observed_rate"].notna()
    inside = (df["predicted_mean"] >= df["bin_lo"] - 1e-9) & (df["predicted_mean"] <= df["bin_hi"] + 1e-9)
    return (df["bin_lo"] < df["bin_hi"]) & ((empty & both_null) | (~empty & both_present & inside))


def _probability_column(description: str, nullable: bool = False) -> Column:
    return Column(
        pa.Float64,
        nullable=nullable,
        coerce=True,
        checks=[Check.in_range(0.0, 1.0)],
        description=description,
    )


def _text_column(description: str) -> Column:
    return Column(
        pa.String,
        nullable=False,
        checks=[Check.str_length(min_value=1)],
        description=description,
    )


mart_aq_trend_monthly_schema = pa.DataFrameSchema(
    columns={
        "station_id": _text_column("Station identifier, declared in the manifest stations catalogue"),
        "month": Column(
            checks=[
                Check(check_is_date, error="month must be a date or datetime"),
                Check(check_first_of_month, error="month must be the first day of a month"),
            ],
            nullable=False,
            description="First day of the month (UTC)",
        ),
        "observed_mean_ugm3": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Mean of the valid daily means in the month; NULL when n_days is 0",
        ),
        "weather_adjusted_mean_ugm3": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Mean after removing the estimated effect of weather; NULL when not estimated",
        ),
        "adjusted_lo_ugm3": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Lower bound of the interval around the adjusted mean",
        ),
        "adjusted_hi_ugm3": Column(
            pa.Float64,
            nullable=True,
            coerce=True,
            checks=[Check.ge(0.0)],
            description="Upper bound of the interval around the adjusted mean",
        ),
        "n_days": Column(
            checks=[
                Check(
                    lambda s: pd.api.types.is_integer_dtype(s),
                    error="n_days must be an integer",
                ),
                Check.in_range(0, 31),
            ],
            nullable=False,
            description="Number of valid days that contributed to the month (0 to 31)",
        ),
        "pollutant": Column(
            pa.String,
            nullable=False,
            checks=[Check.isin(VALID_POLLUTANTS)],
            description="Pollutant identifier",
        ),
    },
    checks=[
        Check(check_trend_consistency, error="trend rows violate the n_days, NULL or interval rules"),
    ],
    unique=["station_id", "pollutant", "month"],
    strict=True,
)

mart_aq_risk_daily_schema = pa.DataFrameSchema(
    columns={
        "station_id": _text_column("Station identifier, declared in the manifest stations catalogue"),
        "target_date": Column(
            checks=[Check(check_is_date, error="target_date must be a date or datetime (EEA day, UTC+1)")],
            nullable=False,
            description="Day being predicted, on the same day convention as mart_aq_daily.day",
        ),
        "issued_at": Column(
            checks=[Check(check_datetime_utc, error="issued_at must be a UTC timestamp")],
            nullable=False,
            description="UTC time the probability was produced",
        ),
        "probability": _probability_column("Predicted probability that the label occurs"),
        "label_id": _text_column("Identifier of the predicted label; its text is in mart_model_card"),
        "split": Column(
            pa.String,
            nullable=False,
            checks=[Check.isin(VALID_SPLITS)],
            description="Data split the row belongs to",
        ),
        "actual_label": Column(
            "Int64",
            nullable=True,
            coerce=True,
            checks=[Check.isin([0, 1])],
            description="Observed outcome (0 or 1); NULL until known or when the day cannot be decided",
        ),
        "model_id": _text_column("Model identifier, declared in mart_model_card"),
    },
    checks=[
        Check(check_issued_before_target_day_ends, error="issued_at must precede the end of the target day"),
    ],
    unique=["station_id", "target_date", "model_id"],
    strict=True,
)

mart_aq_risk_drivers_schema = pa.DataFrameSchema(
    columns={
        "station_id": _text_column("Station identifier"),
        "target_date": Column(
            checks=[Check(check_is_date, error="target_date must be a date or datetime (EEA day, UTC+1)")],
            nullable=False,
            description="Day being predicted",
        ),
        "model_id": _text_column("Model identifier"),
        "feature": _text_column("Feature name, as listed in mart_model_card.features"),
        "contribution": Column(
            pa.Float64,
            nullable=False,
            coerce=True,
            checks=[Check(check_finite, error="contribution must be finite")],
            description="Signed contribution on the log-odds scale",
        ),
        "rank": Column(
            checks=[
                Check(
                    lambda s: pd.api.types.is_integer_dtype(s),
                    error="rank must be an integer",
                ),
                Check.ge(1),
            ],
            nullable=False,
            description="Rank by absolute contribution within the prediction, 1 being the largest",
        ),
    },
    checks=[
        Check(check_drivers_ranking, error="rank must run 1..k and follow absolute contribution"),
    ],
    unique=["station_id", "target_date", "model_id", "feature"],
    strict=True,
)

mart_model_card_schema = pa.DataFrameSchema(
    columns={
        "model_id": _text_column("Model identifier"),
        "algorithm": _text_column("Algorithm name"),
        "trained_at": Column(
            checks=[Check(check_datetime_utc, error="trained_at must be a UTC timestamp")],
            nullable=False,
            description="UTC time training finished",
        ),
        "train_start": Column(checks=[Check(check_is_date, error="train_start must be a date")], nullable=False),
        "train_end": Column(checks=[Check(check_is_date, error="train_end must be a date")], nullable=False),
        "validation_start": Column(checks=[Check(check_is_date, error="validation_start must be a date")], nullable=False),
        "validation_end": Column(checks=[Check(check_is_date, error="validation_end must be a date")], nullable=False),
        "test_start": Column(checks=[Check(check_is_date, error="test_start must be a date")], nullable=False),
        "test_end": Column(checks=[Check(check_is_date, error="test_end must be a date")], nullable=False),
        "label_id": _text_column("Identifier of the predicted label"),
        "label_text": _text_column("Plain-language definition of the label"),
        "features": Column(
            checks=[Check(check_string_list, error="features must be a non-empty list of distinct strings")],
            nullable=False,
            description="Input feature names",
        ),
        "roc_auc_test": _probability_column("ROC AUC on the test window", nullable=True),
        "pr_auc_test": _probability_column("Precision-recall AUC on the test window", nullable=True),
        "brier_test": _probability_column("Brier score on the test window", nullable=True),
        "baseline_roc_auc_test_climatology": _probability_column(
            "ROC AUC of a climatology baseline on the test window", nullable=True
        ),
        "baseline_roc_auc_test_persistence": _probability_column(
            "ROC AUC of a persistence baseline on the test window", nullable=True
        ),
        "threshold": _probability_column("Decision threshold for a predicted exceedance"),
        "precision_test": _probability_column("Precision at the threshold on the test window", nullable=True),
        "recall_test": _probability_column("Recall at the threshold on the test window", nullable=True),
        "uses_forecast_weather": Column(
            pa.Bool,
            nullable=False,
            description="True when some inputs are weather forecasts available at issue time",
        ),
        "limitations": _text_column("Known limitations in plain language"),
    },
    checks=[
        Check(check_model_windows, error="windows must be ordered, and training must end before the test window starts"),
    ],
    unique=["model_id"],
    strict=True,
)

mart_model_calibration_schema = pa.DataFrameSchema(
    columns={
        "model_id": _text_column("Model identifier"),
        "bin_lo": _probability_column("Lower bin edge"),
        "bin_hi": _probability_column("Upper bin edge"),
        "predicted_mean": _probability_column("Mean predicted probability in the bin", nullable=True),
        "observed_rate": _probability_column("Observed share of positive days in the bin", nullable=True),
        "n": Column(
            checks=[
                Check(
                    lambda s: pd.api.types.is_integer_dtype(s),
                    error="n must be an integer",
                ),
                Check.ge(0),
            ],
            nullable=False,
            description="Number of test days in the bin",
        ),
    },
    checks=[
        Check(check_calibration_rows, error="calibration rows violate the bin or NULL rules"),
        Check(check_calibration_bins, error="calibration bins must be contiguous and cover 0 to 1"),
    ],
    unique=["model_id", "bin_lo"],
    strict=True,
)

OPTIONAL_MART_SCHEMAS = {
    "mart_aq_trend_monthly": mart_aq_trend_monthly_schema,
    "mart_aq_risk_daily": mart_aq_risk_daily_schema,
    "mart_aq_risk_drivers": mart_aq_risk_drivers_schema,
    "mart_model_card": mart_model_card_schema,
    "mart_model_calibration": mart_model_calibration_schema,
}
