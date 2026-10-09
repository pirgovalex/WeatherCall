"""Capture raw Open-Meteo forecast and CAMS air-quality responses into the landing zone.

Each source is captured independently so that one failing endpoint never costs the
other source's (irreplaceable) forecast vintage. Landing files are written atomically.
The process exits non-zero if any capture failed or a unit guard tripped, while still
keeping whatever raw data was received.
"""

import gzip
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import requests

# Project Root based on the location of this script
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LANDING_DIR = os.path.join(PROJECT_ROOT, "data", "landing")
OPS_DIR = os.path.join(PROJECT_ROOT, "data", "ops")
RUN_LOG_NAME = "ingest_runs.jsonl"

# Ensure src directory is on sys.path when invoked directly
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from plovdiv.config import settings  # noqa: E402

log = logging.getLogger("plovdiv.extract.fetch_forecasts")

MAX_RETRY_DELAY_S = 300

# The contract (contracts/openapi.yaml, manifest variables) declares wind in m/s.
# Open-Meteo defaults to km/h, so request m/s and verify the echoed units.
EXPECTED_WEATHER_UNITS = {"wind_speed_10m": "m/s", "wind_gusts_10m": "m/s"}


def _retry_delay(resp, attempt):
    """Seconds to wait after a 429. Retry-After may be absent or an HTTP date."""
    raw = resp.headers.get("Retry-After")
    try:
        delay = int(raw) if raw is not None else 2 ** attempt
    except ValueError:
        delay = 2 ** attempt
    return max(0, min(delay, MAX_RETRY_DELAY_S))


def fetch_with_backoff(url, params, max_retries=5):
    """Fetch JSON from Open-Meteo with exponential backoff and 429 handling.

    Raises after max_retries attempts instead of returning None. Client errors
    other than 429 are not retried. The caller's params dict is not modified.
    """
    params = dict(params)
    headers = {"User-Agent": settings.user_agent}

    # Inject API key if provided via .env
    if settings.openmeteo_api_key:
        params["apikey"] = settings.openmeteo_api_key

    for attempt in range(max_retries):
        last_attempt = attempt == max_retries - 1
        try:
            log.info("Fetching %s (attempt %d)", url, attempt + 1)
            resp = requests.get(url, params=params, headers=headers, timeout=15)

            if resp.status_code == 429:
                if last_attempt:
                    raise RuntimeError(f"Rate limited (429) after {max_retries} attempts: {url}")
                delay = _retry_delay(resp, attempt)
                log.warning("Rate limited (429). Retrying after %d seconds", delay)
                time.sleep(delay)
                continue

            resp.raise_for_status()  # 4xx (except 429) is re-raised below without retry
            return resp.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status is not None and 400 <= status < 500:
                raise
            if last_attempt:
                raise
            log.warning("Request failed: %s", e)
            time.sleep(2 ** attempt)
        except requests.exceptions.RequestException as e:
            log.warning("Request failed: %s", e)
            if last_attempt:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"fetch_with_backoff exhausted retries without a result: {url}")


def save_landing_data(source, endpoint, data, request_params=None):
    """Save the raw JSON payload into the partitioned landing zone as GZIP, atomically."""
    now = datetime.now(timezone.utc)
    fetched_date = now.strftime("%Y-%m-%d")
    run_id = str(uuid.uuid4())

    # Inject fetch time for auditing; never persist the API key.
    audit = {"fetched_at_utc": now.isoformat(), "run_id": run_id}
    if request_params is not None:
        audit["request_params"] = {k: v for k, v in request_params.items() if k != "apikey"}
    data["_audit"] = audit

    partition_path = os.path.join(
        LANDING_DIR, f"source={source}", f"endpoint={endpoint}", f"fetched_date={fetched_date}"
    )
    os.makedirs(partition_path, exist_ok=True)

    file_path = os.path.join(partition_path, f"{run_id}.json.gz")
    tmp_path = file_path + ".tmp"
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, file_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    log.info("Saved %s/%s payload to %s", source, endpoint, file_path)
    return file_path


def record_run(entry):
    """Append one capture attempt to the run log (one JSON object per line).

    The log is what makes outages visible: a gap with no entries means the program did not
    run, a gap with failed entries means it ran and could not capture.
    """
    try:
        os.makedirs(OPS_DIR, exist_ok=True)
        with open(os.path.join(OPS_DIR, RUN_LOG_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as e:
        log.error("Could not write the run log: %s", e)


def check_units(data, expected):
    """Return a list of human-readable unit mismatches (empty if all as expected)."""
    units = data.get("hourly_units", {})
    return [
        f"{name}: expected {want!r}, got {units.get(name)!r}"
        for name, want in expected.items()
        if units.get(name) != want
    ]


def _display_path(path: str) -> str:
    """Return the path relative to the project root, or the absolute path when it is on another drive."""
    try:
        shown = os.path.relpath(path, PROJECT_ROOT)
    except ValueError:  # Windows: the landing directory is on a different drive than the project
        shown = os.path.abspath(path)
    return shown.replace(os.sep, "/")


def _capture(source, endpoint, url, params, expected_units=None):
    """Fetch and land one source. Returns a list of problems (empty on success)."""
    started = datetime.now(timezone.utc)
    entry = {
        "source": source,
        "endpoint": endpoint,
        "started_at_utc": started.isoformat(),
        "status": "ok",
        "landing_file": None,
        "error": None,
    }

    def finish(problems):
        entry["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        record_run(entry)
        return problems

    try:
        data = fetch_with_backoff(url, params)
    except Exception as e:  # keep going so the other source is still captured
        log.error("Capture failed for %s/%s: %s", source, endpoint, e)
        entry.update(status="failed", error=f"fetch failed: {e}"[:300])
        return finish([f"{source}/{endpoint}: fetch failed: {e}"])

    try:
        path = save_landing_data(source, endpoint, data, request_params=params)
    except Exception as e:
        log.error("Could not write landing file for %s/%s: %s", source, endpoint, e)
        entry.update(status="failed", error=f"write failed: {e}"[:300])
        return finish([f"{source}/{endpoint}: write failed: {e}"])
    entry["landing_file"] = _display_path(path)

    # The raw file is kept even when a guard trips; the run is still reported as failed.
    mismatches = check_units(data, expected_units or {})
    for m in mismatches:
        log.error("Unit guard %s/%s: %s", source, endpoint, m)
    if mismatches:
        entry.update(status="unit_mismatch", error="; ".join(mismatches)[:300])
    return finish([f"{source}/{endpoint}: unit mismatch: {m}" for m in mismatches])


def run_forecast_capture():
    """Main capture function for Days 1-2 deliverables. Returns a list of problems."""
    log.info("Starting forecast capture...")

    weather_params = {
        "latitude": settings.latitude,
        "longitude": settings.longitude,
        "models": "ecmwf_ifs025",
        "past_days": 3,
        "forecast_days": 7,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,precipitation,surface_pressure,shortwave_radiation,cape,lifted_index",
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    aq_params = {
        "latitude": settings.latitude,
        "longitude": settings.longitude,
        "domains": "cams_europe",
        "past_days": 7,
        "hourly": "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,ozone,sulphur_dioxide,european_aqi",
        "timezone": "GMT",
    }

    # CAMS first: its forecast vintages cannot be reconstructed later.
    problems = []
    problems += _capture("cams", "air_quality", settings.aq_api_url, aq_params)
    problems += _capture(
        "openmeteo", "forecast", settings.weather_api_url, weather_params, EXPECTED_WEATHER_UNITS
    )

    if problems:
        log.error("Forecast capture finished with %d problem(s)", len(problems))
    else:
        log.info("Forecast capture complete.")
    return problems


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(1 if run_forecast_capture() else 0)
