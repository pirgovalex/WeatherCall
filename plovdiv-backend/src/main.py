import os
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from .errors import ApiError, invalid, not_found, register_error_handlers
from .models import (
    AqRiskResponse,
    AqTrendResponse,
    DailyAqResponse,
    ErrorEnvelope,
    HealthResponse,
    MetaResponse,
    ModelCalibrationResponse,
    ModelCardResponse,
    Pollutant,
    Resolution,
    SeriesResponse,
    SourceType,
    Split,
)
from .validation import (
    check_day_range,
    check_identifier,
    check_instant_range,
    check_location,
    check_station,
    parse_calendar_date,
    parse_day,
    parse_instant,
)
from .warehouse import (
    CALIBRATION_MART,
    CARD_MART,
    DEFAULT_MARTS_DIR,
    DRIVERS_MART,
    RISK_MART,
    TREND_MART,
    Snapshot,
    Warehouse,
    finite,
    utc_text,
)

# Pollutants served by /api/v1/aq/daily (see contracts/openapi.yaml).
AQ_POLLUTANTS = ["pm2_5", "pm10", "no2"]
DEFAULT_SOURCE_TYPE = "analysis"

ERROR_RESPONSES = {404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}}
OPTIONAL_MART_ERROR_RESPONSES = {**ERROR_RESPONSES, 503: {"model": ErrorEnvelope}}

# Longest date range, in days, accepted by the new endpoints (see contracts/openapi.yaml).
MAX_TREND_DAYS = 7320
MAX_RISK_DAYS = 366


DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]


def parse_allowed_origins(origins_str: Optional[str] = None) -> List[str]:
    """Parse comma-separated origin strings into a list of origins."""
    if origins_str is None:
        origins_str = os.getenv("ALLOWED_ORIGINS") or os.getenv("CORS_ORIGINS")
    if not origins_str:
        warnings.warn(
            "ALLOWED_ORIGINS env var is not set. Falling back to localhost defaults. "
            "Set ALLOWED_ORIGINS explicitly in production.",
            stacklevel=2,
        )
        return list(DEFAULT_ALLOWED_ORIGINS)
    return [origin.strip() for origin in origins_str.split(",") if origin.strip()]


def resolve_marts_dir(marts_dir: Optional[Path | str] = None) -> Path:
    """Marts directory: explicit argument, then MARTS_DIR, then the copied contract fixtures."""
    if marts_dir is not None:
        return Path(marts_dir)
    env_value = os.getenv("MARTS_DIR")
    return Path(env_value) if env_value else DEFAULT_MARTS_DIR


def get_warehouse(request: Request) -> Warehouse:
    warehouse = getattr(request.app.state, "warehouse", None)
    if warehouse is None:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "The warehouse is not loaded.")
    return warehouse


def create_app(allowed_origins: Optional[List[str]] = None, marts_dir: Optional[Path | str] = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    marts_path = resolve_marts_dir(marts_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Raises StartupError (and so stops the server) if the manifest or marts are unusable.
        warehouse = Warehouse(marts_path)
        app.state.warehouse = warehouse
        try:
            yield
        finally:
            warehouse.close()

    # Serve /docs and /redoc in development only
    env = os.getenv("ENV", "development")
    docs_url = "/docs" if env == "development" else None
    redoc_url = "/redoc" if env == "development" else None

    app = FastAPI(
        title="Plovdiv Weather and Air Quality Analytics API",
        description="Read-only analytics API for the Plovdiv weather and air quality warehouse against data contracts.",
        version="1",
        docs_url=docs_url,
        redoc_url=redoc_url,
        lifespan=lifespan,
    )
    register_error_handlers(app)

    if allowed_origins is None:
        allowed_origins = parse_allowed_origins()

    # Per CORS specification, wildcard '*' cannot be combined with allow_credentials=True
    allow_credentials = "*" not in allowed_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        # Read-only methods only: this API has no write endpoints
        allow_methods=["GET", "OPTIONS"],
        # Minimal set of request headers
        allow_headers=["Accept", "Accept-Language", "Content-Type"],
    )

    @app.get(
        "/healthz",
        response_model=HealthResponse,
        summary="Liveness health check",
        description="Returns service operational status.",
        operation_id="getHealthz",
        tags=["Health"],
    )
    async def get_healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/api/v1/meta",
        response_model=MetaResponse,
        response_model_exclude_unset=True,
        responses={500: {"model": ErrorEnvelope}},
        summary="Warehouse execution metadata",
        description="Returns schema_version, run_id, generated_at_utc, attribution, the variable catalogue, and data ranges per mart.",
        operation_id="getMeta",
        tags=["Data"],
    )
    def get_meta(warehouse: Warehouse = Depends(get_warehouse)):
        return warehouse.current().manifest

    @app.get(
        "/api/v1/series",
        response_model=SeriesResponse,
        responses=ERROR_RESPONSES,
        summary="Columnar time series data",
        description=(
            "Returns columnar time series for a single variable and location. Timestamps are epoch seconds UTC "
            "and values preserve null for gaps. `to` is inclusive. `daily` resolution returns the mean of the "
            "non-null hourly values for each UTC day. When `source_type` is omitted, `analysis` is used."
        ),
        operation_id="getSeries",
        tags=["Data"],
    )
    def get_series(
        variable: Annotated[str, Query(description="Variable identifier defined in the manifest catalogue (e.g. pm2_5, temperature_2m).")],
        location: Annotated[str, Query(description="Location identifier (e.g. plovdiv).")],
        start: Annotated[str, Query(alias="from", description="Query range start timestamp in ISO-8601 UTC.")],
        end: Annotated[str, Query(alias="to", description="Query range end timestamp in ISO-8601 UTC. Maximum span is 366 days.")],
        resolution: Annotated[Resolution, Query(description="Series temporal resolution.")],
        source_type: Annotated[Optional[SourceType], Query(description="Provenance classification of data.")] = None,
        warehouse: Warehouse = Depends(get_warehouse),
    ):
        # All parameter checks come first: nothing below this block touches the database.
        check_identifier(variable, "variable")
        check_location(location)
        first = parse_instant(start, "from")
        last = parse_instant(end, "to")
        check_instant_range(first, last)

        snapshot: Snapshot = warehouse.current()
        if variable not in snapshot.variables:
            raise not_found(f"Variable '{variable}' is not in the catalogue.")
        if location not in snapshot.locations:
            raise not_found(f"Location '{location}' was not found.")

        chosen = source_type or DEFAULT_SOURCE_TYPE
        timestamps, values = warehouse.fetch_series(
            snapshot, variable, location, chosen, int(first.timestamp()), int(last.timestamp()), resolution
        )
        return {
            "variable": variable,
            "unit": snapshot.variables[variable]["unit"],
            "location_id": location,
            "resolution": resolution,
            "source_type": chosen,
            "timestamps": timestamps,
            "values": values,
            "warehouse": snapshot.warehouse_info,
            "attribution": snapshot.manifest["attribution"],
        }

    @app.get(
        "/api/v1/aq/daily",
        response_model=DailyAqResponse,
        responses=ERROR_RESPONSES,
        summary="Daily air quality aggregated series",
        description="Returns daily air quality summary metrics (mean, valid_hours, is_valid_day) on EEA day convention (UTC+1).",
        operation_id="getAqDaily",
        tags=["Data"],
    )
    def get_aq_daily(
        pollutant: Annotated[str, Query(description="Air quality pollutant identifier.", json_schema_extra={"enum": AQ_POLLUTANTS})],
        location: Annotated[str, Query(description="Location identifier (e.g. plovdiv).")],
        start: Annotated[str, Query(alias="from", description="Start date or timestamp in ISO-8601 UTC.")],
        end: Annotated[str, Query(alias="to", description="End date or timestamp in ISO-8601 UTC. Maximum span is 366 days.")],
        warehouse: Warehouse = Depends(get_warehouse),
    ):
        check_identifier(pollutant, "pollutant")
        check_location(location)
        first = parse_day(start, "from")
        last = parse_day(end, "to")
        check_day_range(first, last)

        snapshot = warehouse.current()
        if pollutant not in AQ_POLLUTANTS or pollutant not in snapshot.variables:
            raise not_found(f"Pollutant '{pollutant}' is not available.")
        if location not in snapshot.locations | snapshot.daily_locations:
            raise not_found(f"Location '{location}' was not found.")

        rows = warehouse.fetch_daily_air_quality(snapshot, pollutant, location, first, last)
        return {
            "pollutant": pollutant,
            "location_id": location,
            "days": [r[0] for r in rows],
            "mean": [r[1] for r in rows],
            "valid_hours": [r[2] for r in rows],
            "is_valid_day": [r[3] for r in rows],
            "warehouse": snapshot.warehouse_info,
            "attribution": snapshot.manifest["attribution"],
        }

    @app.get(
        "/api/v1/aq/trend",
        response_model=AqTrendResponse,
        responses=OPTIONAL_MART_ERROR_RESPONSES,
        summary="Monthly air quality trend per station",
        description=(
            "Monthly observed and weather-adjusted means for one station and pollutant, with a lower and upper "
            "bound for the adjusted mean. A month is returned when any part of it falls inside from..to (UTC "
            "calendar dates, both inclusive). The maximum span is 7320 days. Returns 503 when mart_aq_trend_monthly "
            "is not declared in the manifest."
        ),
        operation_id="getAqTrend",
        tags=["Data"],
    )
    def get_aq_trend(
        station_id: Annotated[str, Query(description="Station identifier from the manifest stations catalogue (for example BG0051A).")],
        start: Annotated[str, Query(alias="from", description="Start date (YYYY-MM-DD) in UTC, inclusive.")],
        end: Annotated[str, Query(alias="to", description="End date (YYYY-MM-DD) in UTC, inclusive. Maximum span is 7320 days.")],
        pollutant: Annotated[Pollutant, Query(description="Air quality pollutant identifier. Defaults to pm10 when omitted.")] = "pm10",
        warehouse: Warehouse = Depends(get_warehouse),
    ):
        check_station(station_id)
        first = parse_calendar_date(start, "from")
        last = parse_calendar_date(end, "to")
        check_day_range(first, last, MAX_TREND_DAYS)

        snapshot = warehouse.current()
        snapshot.require_mart(TREND_MART)
        if station_id not in snapshot.stations:
            raise not_found(f"Station '{station_id}' was not found.")

        rows = warehouse.fetch_trend(snapshot, station_id, pollutant, first, last)
        return {
            "station_id": station_id,
            "pollutant": pollutant,
            "unit": snapshot.variables[pollutant]["unit"],
            "months": [r[0] for r in rows],
            "observed_mean": [finite(r[1]) for r in rows],
            "weather_adjusted_mean": [finite(r[2]) for r in rows],
            "adjusted_lo": [finite(r[3]) for r in rows],
            "adjusted_hi": [finite(r[4]) for r in rows],
            "n_days": [r[5] for r in rows],
            "warehouse": snapshot.warehouse_info,
            "attribution": snapshot.manifest["attribution"],
        }

    @app.get(
        "/api/v1/aq/risk",
        response_model=AqRiskResponse,
        response_model_exclude_unset=True,
        responses=OPTIONAL_MART_ERROR_RESPONSES,
        summary="Daily exceedance probability per station",
        description=(
            "Probability that the label described in the model card occurs on each target date (EEA day), with the "
            "split of the row and, once known, the actual outcome. In range mode from and to are required (inclusive, "
            "at most 366 days) and rows are ordered by target date. With latest=true, from and to must be omitted and "
            "the response holds the row with the most recent issued_at together with its drivers. Returns 503 when a "
            "required mart (mart_aq_risk_daily, and mart_aq_risk_drivers for latest=true) is not declared."
        ),
        operation_id="getAqRisk",
        tags=["Data"],
    )
    def get_aq_risk(
        station_id: Annotated[str, Query(description="Station identifier from the manifest stations catalogue (for example BG0078A).")],
        start: Annotated[Optional[str], Query(alias="from", description="Start target date (YYYY-MM-DD), inclusive. Required unless latest=true, and not allowed with latest=true.")] = None,
        end: Annotated[Optional[str], Query(alias="to", description="End target date (YYYY-MM-DD), inclusive. Required unless latest=true, and not allowed with latest=true. Maximum span is 366 days.")] = None,
        split: Annotated[Optional[Split], Query(description="Restrict rows to one data split.")] = None,
        model_id: Annotated[Optional[str], Query(description="Restrict rows to one model. When omitted, rows of every model in the mart are returned.")] = None,
        latest: Annotated[bool, Query(description="When true, return only the most recently issued row with its drivers instead of a range.")] = False,
        warehouse: Warehouse = Depends(get_warehouse),
    ):
        check_station(station_id)
        if model_id is not None:
            check_identifier(model_id, "model_id")
        first = last = None
        if latest:
            if start is not None or end is not None:
                raise invalid("'from' and 'to' must be omitted when latest=true.")
        else:
            if start is None or end is None:
                raise invalid("'from' and 'to' are required unless latest=true.")
            first = parse_calendar_date(start, "from")
            last = parse_calendar_date(end, "to")
            check_day_range(first, last, MAX_RISK_DAYS)

        snapshot = warehouse.current()
        snapshot.require_mart(RISK_MART)
        if latest:
            snapshot.require_mart(DRIVERS_MART)
        if station_id not in snapshot.stations:
            raise not_found(f"Station '{station_id}' was not found.")
        if model_id is not None and model_id not in warehouse.model_ids(snapshot):
            raise not_found(f"Model '{model_id}' was not found.")

        drivers = None
        if latest:
            rows = warehouse.fetch_risk_latest(snapshot, station_id, split, model_id)
            found = warehouse.fetch_risk_drivers(snapshot, station_id, rows[0][0], rows[0][6]) if rows else []
            drivers = {
                "feature": [d[0] for d in found],
                "contribution": [float(d[1]) for d in found],
                "rank": [d[2] for d in found],
            }
        else:
            rows = warehouse.fetch_risk_range(snapshot, station_id, first, last, split, model_id)

        body = {
            "station_id": station_id,
            "target_dates": [r[0] for r in rows],
            "issued_at": [utc_text(r[1]) for r in rows],
            "probability": [float(r[2]) for r in rows],
            "label_id": [r[3] for r in rows],
            "split": [r[4] for r in rows],
            "actual_label": [r[5] for r in rows],
            "model_id": [r[6] for r in rows],
            "warehouse": snapshot.warehouse_info,
            "attribution": snapshot.manifest["attribution"],
        }
        if drivers is not None:
            body["drivers"] = drivers
        return body

    @app.get(
        "/api/v1/model/card",
        response_model=ModelCardResponse,
        responses=OPTIONAL_MART_ERROR_RESPONSES,
        summary="Model card",
        description=(
            "Model card for one model: algorithm, training, validation and test windows, the label definition, "
            "input features, test metrics, baseline comparisons, the decision threshold and known limitations. "
            "Returns 503 when mart_model_card is not declared in the manifest."
        ),
        operation_id="getModelCard",
        tags=["Data"],
    )
    def get_model_card(
        model_id: Annotated[str, Query(description="Model identifier, as returned in the model_id array of /api/v1/aq/risk.")],
        warehouse: Warehouse = Depends(get_warehouse),
    ):
        check_identifier(model_id, "model_id")
        snapshot = warehouse.current()
        snapshot.require_mart(CARD_MART)
        row = warehouse.fetch_model_card(snapshot, model_id)
        if row is None:
            raise not_found(f"Model '{model_id}' was not found.")
        return {
            "model_id": row[0],
            "algorithm": row[1],
            "trained_at": utc_text(row[2]),
            "windows": {
                "train_start": row[3],
                "train_end": row[4],
                "validation_start": row[5],
                "validation_end": row[6],
                "test_start": row[7],
                "test_end": row[8],
            },
            "label_id": row[9],
            "label_text": row[10],
            "features": list(row[11]),
            "uses_forecast_weather": row[12],
            "threshold": row[13],
            "metrics": {
                "roc_auc_test": finite(row[14]),
                "pr_auc_test": finite(row[15]),
                "brier_test": finite(row[16]),
                "precision_test": finite(row[17]),
                "recall_test": finite(row[18]),
            },
            "baselines": {
                "roc_auc_test_climatology": finite(row[19]),
                "roc_auc_test_persistence": finite(row[20]),
            },
            "limitations": row[21],
            "warehouse": snapshot.warehouse_info,
            "attribution": snapshot.manifest["attribution"],
        }

    @app.get(
        "/api/v1/model/calibration",
        response_model=ModelCalibrationResponse,
        responses=OPTIONAL_MART_ERROR_RESPONSES,
        summary="Model calibration bins",
        description=(
            "Reliability-diagram bins for one model, computed on the test split, ordered by bin_lo. A bin with n "
            "equal to 0 has null predicted_mean and observed_rate. Returns 503 when mart_model_calibration is not "
            "declared in the manifest."
        ),
        operation_id="getModelCalibration",
        tags=["Data"],
    )
    def get_model_calibration(
        model_id: Annotated[str, Query(description="Model identifier, as returned in the model_id array of /api/v1/aq/risk.")],
        warehouse: Warehouse = Depends(get_warehouse),
    ):
        check_identifier(model_id, "model_id")
        snapshot = warehouse.current()
        snapshot.require_mart(CALIBRATION_MART)
        rows = warehouse.fetch_calibration(snapshot, model_id)
        # With no bins the model is unknown unless the model card lists it.
        if not rows and (CARD_MART not in snapshot.optional_globs or model_id not in warehouse.model_ids(snapshot)):
            raise not_found(f"Model '{model_id}' was not found.")
        return {
            "model_id": model_id,
            "bin_lo": [r[0] for r in rows],
            "bin_hi": [r[1] for r in rows],
            "predicted_mean": [finite(r[2]) for r in rows],
            "observed_rate": [finite(r[3]) for r in rows],
            "n": [r[4] for r in rows],
            "warehouse": snapshot.warehouse_info,
            "attribution": snapshot.manifest["attribution"],
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # Bind to localhost only; auto-reload is opt-in via RELOAD=true
    reload = os.getenv("RELOAD", "false").lower() == "true"
    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=reload)
