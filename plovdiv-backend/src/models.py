"""Response models. Field names and shapes follow contracts/openapi.yaml."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

SourceType = Literal["station", "analysis", "reanalysis_provisional", "reanalysis_final", "forecast"]
Resolution = Literal["hourly", "daily"]


class HealthResponse(BaseModel):
    status: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class WarehouseMeta(BaseModel):
    run_id: str
    schema_version: str
    generated_at_utc: str


class VariableCatalogueEntry(BaseModel):
    unit: str
    label: str


class MartRangeHourly(BaseModel):
    glob: str
    rows: int
    min_valid_at_utc: str
    max_valid_at_utc: str
    # Optional: latest hour with source_type analysis. Left out of the response when the manifest omits it.
    max_analysis_valid_at_utc: Optional[str] = None


class MartRangeDaily(BaseModel):
    glob: str
    rows: int
    # Optional: first and last EEA day with a row in mart_aq_daily (YYYY-MM-DD).
    min_day: Optional[str] = None
    max_day: Optional[str] = None


class MartRangeMonthly(BaseModel):
    glob: str
    rows: int
    min_month: str
    max_month: str


class MartRangeTargetDate(BaseModel):
    glob: str
    rows: int
    min_target_date: str
    max_target_date: str


class MartRowCount(BaseModel):
    glob: str
    rows: int


class StationEntry(BaseModel):
    label: str
    station_type: str
    area_type: str
    location_id: str


class MartsCatalogue(BaseModel):
    mart_weather_aq_hourly: MartRangeHourly
    mart_aq_daily: MartRangeDaily
    # Optional marts: present only when the pipeline published them.
    mart_aq_trend_monthly: Optional[MartRangeMonthly] = None
    mart_aq_risk_daily: Optional[MartRangeTargetDate] = None
    mart_aq_risk_drivers: Optional[MartRowCount] = None
    mart_model_card: Optional[MartRowCount] = None
    mart_model_calibration: Optional[MartRowCount] = None


class MetaResponse(BaseModel):
    schema_version: str
    run_id: str
    generated_at_utc: str
    attribution: list[str]
    variables: dict[str, VariableCatalogueEntry]
    marts: MartsCatalogue
    stations: Optional[dict[str, StationEntry]] = None


class SeriesResponse(BaseModel):
    variable: str
    unit: str
    location_id: str
    resolution: Resolution
    source_type: SourceType
    timestamps: list[int]
    values: list[Optional[float]]
    warehouse: WarehouseMeta
    attribution: list[str]


class DailyAqResponse(BaseModel):
    pollutant: str
    location_id: str
    days: list[str]
    mean: list[Optional[float]]
    valid_hours: list[int]
    is_valid_day: list[bool]
    warehouse: WarehouseMeta
    attribution: list[str]


Pollutant = Literal["pm2_5", "pm10", "no2"]
Split = Literal["train", "validation", "test", "live"]


class AqTrendResponse(BaseModel):
    station_id: str
    pollutant: Pollutant
    unit: str
    months: list[str]
    observed_mean: list[Optional[float]]
    weather_adjusted_mean: list[Optional[float]]
    adjusted_lo: list[Optional[float]]
    adjusted_hi: list[Optional[float]]
    n_days: list[int]
    warehouse: WarehouseMeta
    attribution: list[str]


class AqRiskDrivers(BaseModel):
    feature: list[str]
    contribution: list[float]
    rank: list[int]


class AqRiskResponse(BaseModel):
    station_id: str
    target_dates: list[str]
    issued_at: list[str]
    probability: list[float]
    label_id: list[str]
    split: list[Split]
    actual_label: list[Optional[int]]
    model_id: list[str]
    # Present only when latest=true.
    drivers: Optional[AqRiskDrivers] = None
    warehouse: WarehouseMeta
    attribution: list[str]


class ModelWindows(BaseModel):
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    test_start: str
    test_end: str


class ModelMetrics(BaseModel):
    roc_auc_test: Optional[float]
    pr_auc_test: Optional[float]
    brier_test: Optional[float]
    precision_test: Optional[float]
    recall_test: Optional[float]


class ModelBaselines(BaseModel):
    roc_auc_test_climatology: Optional[float]
    roc_auc_test_persistence: Optional[float]


class ModelCardResponse(BaseModel):
    model_id: str
    algorithm: str
    trained_at: str
    windows: ModelWindows
    label_id: str
    label_text: str
    features: list[str]
    uses_forecast_weather: bool
    threshold: float
    metrics: ModelMetrics
    baselines: ModelBaselines
    limitations: str
    warehouse: WarehouseMeta
    attribution: list[str]


class ModelCalibrationResponse(BaseModel):
    model_id: str
    bin_lo: list[float]
    bin_hi: list[float]
    predicted_mean: list[Optional[float]]
    observed_rate: list[Optional[float]]
    n: list[int]
    warehouse: WarehouseMeta
    attribution: list[str]
