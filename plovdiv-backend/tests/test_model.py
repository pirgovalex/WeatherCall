"""GET /api/v1/model/card and GET /api/v1/model/calibration"""

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.warehouse import utc_text

from .conftest import assert_envelope, edit_manifest, mart_glob, query
from .test_contract_conformance import validate

CURRENT = "pm10_exceed_gbm_v1"
OLD = "pm10_exceed_logreg_v0"


def card(client, model_id=CURRENT):
    params = {} if model_id is None else {"model_id": model_id}
    return client.get("/api/v1/model/card", params=params)


def calibration(client, model_id=CURRENT):
    params = {} if model_id is None else {"model_id": model_id}
    return client.get("/api/v1/model/calibration", params=params)


def raw_card(model_id):
    return query(
        "select algorithm, epoch_us(trained_at), strftime(train_start, '%Y-%m-%d'), strftime(test_end, '%Y-%m-%d'), "
        "label_id, label_text, features, uses_forecast_weather, threshold, roc_auc_test, pr_auc_test, brier_test, "
        "precision_test, recall_test, baseline_roc_auc_test_climatology, baseline_roc_auc_test_persistence, limitations "
        "from read_parquet(?) where model_id = ?",
        mart_glob("mart_model_card"), model_id,
    )[0]


def test_card_matches_the_mart_and_validates(client, manifest):
    res = card(client)
    assert res.status_code == 200
    body = res.json()
    validate(body, "ModelCardResponse")
    r = raw_card(CURRENT)
    assert body["model_id"] == CURRENT
    assert body["algorithm"] == r[0]
    assert body["trained_at"] == utc_text(r[1]) == "2025-12-31T21:15:00Z"
    assert body["windows"]["train_start"] == r[2]
    assert body["windows"]["test_end"] == r[3]
    assert set(body["windows"]) == {"train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end"}
    assert (body["label_id"], body["label_text"]) == (r[4], r[5])
    assert body["features"] == list(r[6]) and len(body["features"]) == 8
    assert body["uses_forecast_weather"] is True
    assert body["threshold"] == r[8]
    assert body["metrics"] == {
        "roc_auc_test": r[9], "pr_auc_test": r[10], "brier_test": r[11], "precision_test": r[12], "recall_test": r[13],
    }
    assert body["baselines"] == {"roc_auc_test_climatology": r[14], "roc_auc_test_persistence": r[15]}
    assert body["limitations"] == r[16]
    assert body["attribution"] == manifest["attribution"]
    assert body["warehouse"]["run_id"] == manifest["run_id"]


def test_card_null_baseline_is_null(client):
    body = card(client, OLD).json()
    validate(body, "ModelCardResponse")
    assert body["baselines"]["roc_auc_test_persistence"] is None
    assert body["baselines"]["roc_auc_test_climatology"] == 0.668
    assert body["uses_forecast_weather"] is False


def test_card_nan_metric_becomes_null(marts_copy):
    import duckdb

    target = marts_copy / "mart_model_card" / "latest" / "mart_model_card.parquet"
    con = duckdb.connect()
    con.execute(
        "COPY (SELECT * REPLACE (CASE WHEN model_id = 'pm10_exceed_gbm_v1' THEN 'NaN'::DOUBLE ELSE brier_test END "
        f"AS brier_test) FROM read_parquet('{target}')) TO '{target}.new' (FORMAT PARQUET)"
    )
    con.close()
    target.with_name(target.name + ".new").replace(target)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        body = card(client).json()
    validate(body, "ModelCardResponse")
    assert body["metrics"]["brier_test"] is None
    assert body["metrics"]["roc_auc_test"] == 0.842


def test_card_unknown_model_is_404(client):
    assert_envelope(card(client, "nope_v9"), 404, "NOT_FOUND")


@pytest.mark.parametrize("model_id", [None, "", "Bad Id", "x'; drop table y", "../etc", "a" * 100])
def test_card_invalid_model_id_is_422(client, model_id):
    assert_envelope(card(client, model_id), 422, "VALIDATION_ERROR")


def test_calibration_matches_the_mart_and_validates(client, manifest):
    res = calibration(client)
    assert res.status_code == 200
    body = res.json()
    validate(body, "ModelCalibrationResponse")
    rows = query(
        "select bin_lo, bin_hi, predicted_mean, observed_rate, n from read_parquet(?) where model_id = ? order by bin_lo",
        mart_glob("mart_model_calibration"), CURRENT,
    )
    assert len(rows) == 10
    assert body["model_id"] == CURRENT
    assert body["bin_lo"] == [r[0] for r in rows]
    assert body["bin_hi"] == [r[1] for r in rows]
    assert body["predicted_mean"] == [r[2] for r in rows]
    assert body["observed_rate"] == [r[3] for r in rows]
    assert body["n"] == [r[4] for r in rows]
    assert body["bin_lo"] == sorted(body["bin_lo"])
    assert body["bin_lo"][1:] == body["bin_hi"][:-1]
    assert body["bin_lo"][0] == 0.0 and body["bin_hi"][-1] == 1.0
    assert body["warehouse"]["run_id"] == manifest["run_id"]


def test_calibration_empty_bin_has_null_rates(client):
    body = calibration(client).json()
    assert body["n"][-1] == 0
    assert body["predicted_mean"][-1] is None and body["observed_rate"][-1] is None
    assert all(v is not None for v in body["predicted_mean"][:-1])


def test_calibration_other_model(client):
    body = calibration(client, OLD).json()
    validate(body, "ModelCalibrationResponse")
    assert body["model_id"] == OLD and body["n"][-1] == 1


def test_calibration_known_model_without_bins_is_empty(marts_copy):
    import duckdb

    target = marts_copy / "mart_model_calibration" / "latest" / "mart_model_calibration.parquet"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM read_parquet('{target}') WHERE model_id <> '{OLD}') TO '{target}.new' (FORMAT PARQUET)")
    con.close()
    target.with_name(target.name + ".new").replace(target)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        body = calibration(client, OLD).json()
        validate(body, "ModelCalibrationResponse")
        for key in ("bin_lo", "bin_hi", "predicted_mean", "observed_rate", "n"):
            assert body[key] == []
        assert_envelope(calibration(client, "nope_v9"), 404, "NOT_FOUND")


def test_calibration_unknown_model_is_404(client):
    assert_envelope(calibration(client, "nope_v9"), 404, "NOT_FOUND")


@pytest.mark.parametrize("model_id", [None, "", "UPPER", "x' OR '1'='1", "a" * 100])
def test_calibration_invalid_model_id_is_422(client, model_id):
    assert_envelope(calibration(client, model_id), 422, "VALIDATION_ERROR")


def test_missing_calibration_mart_is_503_only_for_calibration(marts_copy):
    edit_manifest(marts_copy, lambda m: m["marts"].pop("mart_model_calibration"))
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        res = calibration(client)
        assert_envelope(res, 503, "SERVICE_UNAVAILABLE")
        assert "mart_model_calibration" in res.json()["error"]["message"]
        assert card(client).status_code == 200


def test_missing_card_mart_is_503(marts_copy):
    def drop(m):
        for name in ("mart_model_calibration", "mart_aq_risk_drivers", "mart_aq_risk_daily", "mart_model_card"):
            m["marts"].pop(name)

    edit_manifest(marts_copy, drop)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        res = card(client)
        assert_envelope(res, 503, "SERVICE_UNAVAILABLE")
        assert "mart_model_card" in res.json()["error"]["message"]
        assert_envelope(calibration(client), 503, "SERVICE_UNAVAILABLE")
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/meta").status_code == 200
        series = client.get(
            "/api/v1/series",
            params={"variable": "pm2_5", "location": "plovdiv", "from": "2026-10-14T00:00:00Z", "to": "2026-10-15T00:00:00Z", "resolution": "hourly"},
        )
        assert series.status_code == 200
        daily = client.get("/api/v1/aq/daily", params={"pollutant": "pm10", "location": "plovdiv", "from": "2026-10-14", "to": "2026-10-20"})
        assert daily.status_code == 200
