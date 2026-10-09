"""Land the EEA up-to-date (UTD) hourly station files that the live risk row needs.

The EEA publishes one Parquet file per station and pollutant with the current year's hourly
values, refreshed during the day. Each run downloads the files into
`data/landing/source=eea/endpoint=utd_hourly/fetch_date=<UTC date>/`, written atomically.
The service replaces its file in place, so a later run on the same day replaces that day's
copy only when the content changed; earlier days stay untouched.
"""

import hashlib
import io
import logging
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from plovdiv.config import settings  # noqa: E402
from plovdiv.extract.fetch_forecasts import LANDING_DIR, _display_path, record_run  # noqa: E402

log = logging.getLogger("plovdiv.extract.fetch_eea_utd")

BASE_URL = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/BG"

# (station, pollutant code): Kamenitsa PM10 is the model's station; Trakia PM10 is kept for the dashboard.
FILES = [
    ("BG0051A", "00005"),
    ("BG0078A", "00005"),
]
REQUIRED_COLUMNS = {"Start", "Value", "Validity"}


def file_name(station: str, pollutant: str) -> str:
    return f"SPO-{station}_{pollutant}_100.parquet"


def _check_parquet(content: bytes) -> int:
    """Return the row count; raise if the payload is not a usable station file."""
    frame = pd.read_parquet(io.BytesIO(content))
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns {sorted(missing)}")
    if frame.empty:
        raise ValueError("file has no rows")
    return len(frame)


def land_one(station: str, pollutant: str, landing_dir: str = LANDING_DIR) -> dict:
    """Download one station file and land it. Returns the run-log entry."""
    name = file_name(station, pollutant)
    now = datetime.now(timezone.utc)
    entry = {
        "source": "eea",
        "endpoint": "utd_hourly",
        "file": name,
        "started_at_utc": now.isoformat(),
        "status": "ok",
        "landing_file": None,
        "error": None,
    }
    try:
        resp = requests.get(f"{BASE_URL}/{name}", headers={"User-Agent": settings.user_agent}, timeout=60)
        resp.raise_for_status()
        rows = _check_parquet(resp.content)

        partition = os.path.join(
            landing_dir, "source=eea", "endpoint=utd_hourly", f"fetch_date={now:%Y-%m-%d}"
        )
        os.makedirs(partition, exist_ok=True)
        path = os.path.join(partition, name)
        digest = hashlib.sha256(resp.content).hexdigest()
        entry.update(rows=rows, sha256=digest, landing_file=_display_path(path))

        if os.path.exists(path):
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == digest:
                    entry["status"] = "unchanged"
                    log.info("%s unchanged since the last run today", name)
                    return entry

        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(resp.content)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        log.info("Landed %s (%d rows) to %s", name, rows, path)
    except Exception as e:
        log.error("EEA UTD capture failed for %s: %s", name, e)
        entry.update(status="failed", error=str(e)[:300])
    finally:
        entry["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        record_run(entry)
    return entry


def run() -> list[str]:
    """Land every configured file. Returns a list of problems (empty on success)."""
    return [
        f"eea/utd_hourly/{e['file']}: {e['error']}"
        for e in (land_one(station, pollutant) for station, pollutant in FILES)
        if e["status"] == "failed"
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(1 if run() else 0)
