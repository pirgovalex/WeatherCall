"""GET /api/v1/aq/risk"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.main import create_app

from .conftest import assert_envelope, edit_manifest, mart_glob, query
from .test_contract_conformance import validate

RANGE = {"station_id": "BG0078A", "from": "2026-10-01", "to": "2026-10-28"}
LATEST = {"station_id": "BG0078A", "latest": "true"}


def get(client, params):
    return client.get("/api/v1/aq/risk", params={k: v for k, v in params.items() if v is not None})


def expected_rows(station, first, last, extra=""):
    return query(
        "select strftime(target_date, '%Y-%m-%d'), epoch_us(issued_at), probability, label_id, split, actual_label, "
        f"model_id from read_parquet(?) where station_id = ? and target_date between ? and ? {extra} "
        "order by target_date, model_id, issued_at",
        mart_glob("mart_aq_risk_daily"), station, first, last,
    )


def iso(micros):
    from src.warehouse import utc_text

    return utc_text(micros)


def test_range_matches_the_mart_and_validates(client, manifest):
    res = get(client, RANGE)
    assert res.status_code == 200
    body = res.json()
    validate(body, "AqRiskResponse")
    rows = expected_rows("BG0078A", "2026-10-01", "2026-10-28")
    assert len(rows) == 28
    assert body["target_dates"] == [r[0] for r in rows]
    assert body["issued_at"] == [iso(r[1]) for r in rows]
    assert body["probability"] == [r[2] for r in rows]
    assert body["label_id"] == [r[3] for r in rows]
    assert body["split"] == [r[4] for r in rows]
    assert body["actual_label"] == [r[5] for r in rows]
    assert body["model_id"] == [r[6] for r in rows]
    assert body["target_dates"] == sorted(body["target_dates"])
    assert all(t.endswith("Z") for t in body["issued_at"])
    assert "drivers" not in body
    assert body["attribution"] == manifest["attribution"]


def test_actual_label_nulls_are_preserved(client):
    labels = get(client, RANGE).json()["actual_label"]
    assert labels.count(None) >= 3
    assert set(labels) <= {None, 0, 1}


def test_split_filter(client):
    body = get(client, {**RANGE, "split": "live"}).json()
    validate(body, "AqRiskResponse")
    assert set(body["split"]) == {"live"}
    assert body["target_dates"][0] == "2026-10-14"
    assert len(body["split"]) == 15


def test_model_filter_with_a_model_that_has_no_rows_is_empty_not_404(client):
    body = get(client, {**RANGE, "model_id": "pm10_exceed_logreg_v0"}).json()
    validate(body, "AqRiskResponse")
    assert body["target_dates"] == [] and body["probability"] == [] and body["model_id"] == []
    assert get(client, {**RANGE, "model_id": "pm10_exceed_gbm_v1"}).json()["model_id"] == ["pm10_exceed_gbm_v1"] * 28


def test_range_bounds_are_inclusive(client):
    body = get(client, {**RANGE, "from": "2026-10-27", "to": "2026-10-27"}).json()
    assert body["target_dates"] == ["2026-10-27"]


def test_known_station_without_matching_rows_is_empty(client):
    body = get(client, {**RANGE, "split": "train"}).json()
    validate(body, "AqRiskResponse")
    assert body["target_dates"] == []
    assert "drivers" not in body


def test_2026_10_25_is_a_single_ordinary_row(client):
    body = get(client, {**RANGE, "from": "2026-10-24", "to": "2026-10-26"}).json()
    assert body["target_dates"] == ["2026-10-24", "2026-10-25", "2026-10-26"]


def test_latest_returns_the_newest_issue_with_ranked_drivers(client):
    res = get(client, LATEST)
    assert res.status_code == 200
    body = res.json()
    validate(body, "AqRiskResponse")
    assert body["target_dates"] == ["2026-10-28"]
    newest = query(
        "select max(epoch_us(issued_at)) from read_parquet(?) where station_id = 'BG0078A'", mart_glob("mart_aq_risk_daily")
    )[0][0]
    assert body["issued_at"] == [iso(newest)]
    assert body["split"] == ["live"]

    drivers = body["drivers"]
    expected = query(
        "select feature, contribution, rank from read_parquet(?) where station_id = 'BG0078A' "
        "and target_date = DATE '2026-10-28' and model_id = 'pm10_exceed_gbm_v1' order by rank",
        mart_glob("mart_aq_risk_drivers"),
    )
    assert len(expected) == 5
    assert drivers["feature"] == [e[0] for e in expected]
    assert drivers["contribution"] == [e[1] for e in expected]
    assert drivers["rank"] == [1, 2, 3, 4, 5]
    magnitudes = [abs(c) for c in drivers["contribution"]]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_latest_with_split_picks_the_newest_row_of_that_split(client):
    body = get(client, {**LATEST, "split": "test"}).json()
    validate(body, "AqRiskResponse")
    assert body["target_dates"] == ["2026-10-13"]
    assert body["split"] == ["test"]
    assert body["drivers"]["rank"] == [1, 2, 3, 4, 5]


def test_latest_for_a_split_without_drivers_has_empty_drivers(client):
    body = get(client, {"station_id": "BG0051A", "latest": "true", "split": "validation"}).json()
    validate(body, "AqRiskResponse")
    assert len(body["target_dates"]) == 1
    assert body["drivers"] == {"feature": [], "contribution": [], "rank": []}


def test_latest_with_no_matching_rows_has_empty_arrays_and_drivers(client):
    body = get(client, {**LATEST, "split": "train"}).json()
    validate(body, "AqRiskResponse")
    assert body["target_dates"] == [] and body["probability"] == []
    assert body["drivers"] == {"feature": [], "contribution": [], "rank": []}


def test_latest_false_behaves_as_range_mode(client):
    assert get(client, {**RANGE, "latest": "false"}).json() == get(client, RANGE).json()
    assert_envelope(get(client, {"station_id": "BG0078A", "latest": "false"}), 422, "VALIDATION_ERROR")


def test_unknown_station_is_404(client):
    assert_envelope(get(client, {**RANGE, "station_id": "BG9999A"}), 404, "NOT_FOUND")
    assert_envelope(get(client, {**LATEST, "station_id": "BG9999A"}), 404, "NOT_FOUND")


def test_unknown_model_is_404(client):
    assert_envelope(get(client, {**RANGE, "model_id": "nope_v9"}), 404, "NOT_FOUND")
    assert_envelope(get(client, {**LATEST, "model_id": "nope_v9"}), 404, "NOT_FOUND")


@pytest.mark.parametrize(
    "overrides",
    [
        {"station_id": None},
        {"station_id": "BG0078A' OR 1=1"},
        {"from": None},
        {"to": None},
        {"from": None, "to": None},
        {"from": "2026-10-28", "to": "2026-10-01"},
        {"from": "2026-13-01"},
        {"to": "2026-10-28T00:00:00Z"},
        {"split": "holdout"},
        {"model_id": "gbm; drop"},
        {"model_id": "A" * 100},
        {"latest": "maybe"},
    ],
)
def test_invalid_parameters_are_422(client, overrides):
    assert_envelope(get(client, {**RANGE, **overrides}), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("extra", [{"from": "2026-10-01"}, {"to": "2026-10-28"}, {"from": "2026-10-01", "to": "2026-10-28"}])
def test_from_or_to_with_latest_is_422(client, extra):
    assert_envelope(get(client, {**LATEST, **extra}), 422, "VALIDATION_ERROR")


def test_range_limit_is_366_days(client):
    last = date(2026, 10, 28)
    ok = get(client, {**RANGE, "from": str(last - timedelta(days=366)), "to": str(last)})
    assert ok.status_code == 200
    too_long = get(client, {**RANGE, "from": str(last - timedelta(days=367)), "to": str(last)})
    assert_envelope(too_long, 422, "VALIDATION_ERROR")


def test_non_finite_probabilities_are_left_out(marts_copy):
    import duckdb

    target = marts_copy / "mart_aq_risk_daily" / "latest" / "mart_aq_risk_daily.parquet"
    con = duckdb.connect()
    con.execute(
        "COPY (SELECT * REPLACE (CASE WHEN target_date = DATE '2026-10-28' THEN 'NaN'::DOUBLE ELSE probability END "
        f"AS probability) FROM read_parquet('{target}')) TO '{target}.new' (FORMAT PARQUET)"
    )
    con.close()
    target.with_name(target.name + ".new").replace(target)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        body = get(client, RANGE).json()
        assert "2026-10-28" not in body["target_dates"]
        validate(body, "AqRiskResponse")
        assert get(client, LATEST).json()["target_dates"] == ["2026-10-27"]


def test_missing_risk_mart_is_503(marts_copy):
    def drop(m):
        for name in ("mart_aq_risk_drivers", "mart_aq_risk_daily"):
            m["marts"].pop(name)

    edit_manifest(marts_copy, drop)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        for params in (RANGE, LATEST):
            res = get(client, params)
            assert_envelope(res, 503, "SERVICE_UNAVAILABLE")
            assert "mart_aq_risk_daily" in res.json()["error"]["message"]
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/aq/trend", params={"station_id": "BG0051A", "from": "2026-01-01", "to": "2026-02-01"}).status_code == 200


def test_missing_drivers_mart_only_affects_latest(marts_copy):
    edit_manifest(marts_copy, lambda m: m["marts"].pop("mart_aq_risk_drivers"))
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        assert get(client, RANGE).status_code == 200
        res = get(client, LATEST)
        assert_envelope(res, 503, "SERVICE_UNAVAILABLE")
        assert "mart_aq_risk_drivers" in res.json()["error"]["message"]
