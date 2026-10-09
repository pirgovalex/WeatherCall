#!/bin/sh
# One full pipeline run: capture, land station data, score the live row, rebuild marts.
# Each step logs its own outcome; a failed capture does not stop the later steps, so the
# marts are always rebuilt from whatever has landed. Run from anywhere; paths are absolute.

PYTHON=/usr/local/bin/python
cd /app/src || exit 1

step() {
    name="$1"; shift
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) pipeline: $name started"
    "$@"
    code=$?
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) pipeline: $name finished with exit $code"
    return $code
}

step "forecast capture" "$PYTHON" -m plovdiv.extract.fetch_forecasts
step "EEA station capture" "$PYTHON" -m plovdiv.extract.fetch_eea_utd
# Model marts first: the live step requires the live model to be in the model card.
step "model marts" "$PYTHON" -m plovdiv.ml.marts
# Exit 2 means the live row was refused (stale or missing inputs); the reason is in data/live/last_run.json.
step "live risk row" "$PYTHON" -m plovdiv.ml.live
step "core marts and manifest" "$PYTHON" -m plovdiv.marts.core
