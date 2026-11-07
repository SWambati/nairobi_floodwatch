#TC-010: isolated FloodWatch unit tests. No live API or production DB access.
import json
from datetime import date
import numpy as np
import pandas as pd
import pytest

from src.model import risk_classification as risk
from src.operational import runtime_features as features
from src.operational import alerts
from src.etl import validation

@pytest.fixture
def risk_classifier(tmp_path):
    path = tmp_path / "risk_thresholds.json"
    path.write_text(json.dumps({"classification_type": "model_score_risk_bands", "moderate_risk": {"threshold": 0.30}, "high_risk": {"threshold": 0.70}}), encoding="utf-8")
    return risk.FloodRiskClassifier(path)

@pytest.fixture
def hourly_data():
    # Seven complete days before 2026-01-08, plus the current day (excluded).
    generic_times = pd.date_range("2026-01-01", periods=192, freq="h")
    generic = pd.DataFrame({"location_id": ["TEST_001"] * len(generic_times), "local_time": generic_times, "precipitation": [1.0] * 168 + [999.0] * 24})
    ecmwf_times = pd.date_range("2026-01-07", periods=48, freq="h")
    ecmwf = pd.DataFrame({"location_id": ["TEST_001"] * len(ecmwf_times), "local_time": ecmwf_times, "temperature_2m": [20.0] * 24 + [99.0] * 24, "relative_humidity_2m": [80.0] * 48, "surface_pressure": [1012.0] * 48, "precipitation": [0.25] * 24 + [100.0] * 24, "soil_moisture_0_to_7cm": [0.35] * 48, "soil_moisture_7_to_28cm": [0.45] * 48})
    return generic, ecmwf

@pytest.fixture
def labels():
    return pd.DataFrame([{
        "label_record_id": "LABEL_001",
        "event_group_id": "EVENT_001",
        "start_date": "2026-01-01",
        "end_date": "2026-01-01",
        "latitude": -1.3,
        "longitude": 36.8,
        "flood_label": 1,
        "verification_status": "candidate",
    }])

@pytest.fixture
def chirps_daily():
    return pd.DataFrame([{
        "label_record_id": "LABEL_001",
        "target_date": "2026-01-08",
        "date": (date(2026, 1, 8) - pd.Timedelta(days=offset)).isoformat(),
        "days_before_target": offset,
        "rainfall_mm": float(offset),
        "chirps_sampling_method": "fixture",
    } for offset in range(1, 8)])

@pytest.fixture
def era5_hourly():
    times = pd.date_range("2026-01-07", periods=24, freq="h")
    return pd.DataFrame({
        "label_record_id": ["LABEL_001"] * 24,
        "timestamp": times,
        "temperature_c": [20.0] * 24,
        "relative_humidity_pct": [75.0] * 24,
        "pressure_hpa": [1010.0] * 24,
        "precipitation_mm": [0.2] * 24,
        "swvl1": [0.3] * 24,
        "swvl2": [0.4] * 24,
    })


# Risk thresholds: positive, boundary and invalid inputs
@pytest.mark.parametrize("score,expected", [
    (0.0, "LOW"), (0.29999, "LOW"),
    (0.30, "MODERATE"), (0.69999, "MODERATE"),
    (0.70, "HIGH"), (1.0, "HIGH"),
])
def test_risk_score_boundaries(risk_classifier, score, expected):
    assert risk_classifier.classify_score(score) == expected

@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01])
def test_risk_rejects_invalid_scores(risk_classifier, score):
    with pytest.raises(ValueError):
        risk_classifier.classify_score(score)


def test_risk_threshold_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        risk.FloodRiskClassifier(tmp_path / "missing.json")


@pytest.mark.parametrize("moderate,high", [
    (0.7, 0.3), (0.4, 0.4), (-0.1, 0.7), (0.3, 1.1),
])
def test_risk_rejects_invalid_config(tmp_path, moderate, high):
    config = tmp_path / "thresholds.json"
    config.write_text(json.dumps({
        "moderate_risk": {"threshold": moderate},
        "high_risk": {"threshold": high},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="thresholds are invalid"):
        risk.FloodRiskClassifier(config)


def test_batch_classification_does_not_mutate_input(risk_classifier):
    input_df = pd.DataFrame({"flood_probability": [0.2, 0.3, 0.7]})
    before = input_df.copy(deep=True)
    result = risk_classifier.classify_batch(input_df)
    assert result["risk_level"].tolist() == ["LOW", "MODERATE", "HIGH"]
    assert result["moderate_threshold"].eq(0.3).all()
    assert result["high_threshold"].eq(0.7).all()
    pd.testing.assert_frame_equal(input_df, before)


@pytest.mark.parametrize("bad", [None, "bad", float("inf"), -0.1, 1.1])
def test_batch_rejects_invalid_scores(risk_classifier, bad):
    with pytest.raises(ValueError):
        risk_classifier.classify_batch(pd.DataFrame({"flood_probability": [bad]}))


def test_batch_rejects_missing_probability_column(risk_classifier):
    with pytest.raises(ValueError, match="missing probability column"):
        risk_classifier.classify_batch(pd.DataFrame({"other": [0.2]}))


def test_risk_threshold_search_and_tie_break():
    preds = pd.DataFrame({
        "flood_label": [0, 1, 0, 1],
        "flood_probability": [0.1, 0.9, 0.4, 0.6],
    })
    search = risk.build_threshold_search(preds)
    assert set(search["threshold"]) == {0.1, 0.4, 0.6, 0.9}
    assert search["predicted_positive"].between(1, 4).all()
    assert 0 <= risk.select_moderate_threshold(search)["f2"] <= 1
    assert 0 <= risk.select_high_threshold(search)["f0_5"] <= 1


# Antecedent-weather feature construction and schema

def test_weather_features_antecedent_only(hourly_data):
    generic, ecmwf = hourly_data
    result = features.build_weather_features(generic, ecmwf, date(2026, 1, 8))
    assert len(result) == 1
    row = result.iloc[0]
    assert row["prediction_date"] == "2026-01-08"
    assert row["rain_1d"] == pytest.approx(24.0)
    assert row["rain_3d"] == pytest.approx(72.0)
    assert row["rain_7d"] == pytest.approx(168.0)
    assert row["temperature_mean_c"] == pytest.approx(20.0)
    assert row["era5_precip_1d_mm"] == pytest.approx(6.0)
    assert row["soil_moisture_layer1_mean"] == pytest.approx(0.35)


def test_weather_rejects_missing_hour(hourly_data):
    generic, ecmwf = hourly_data
    generic = generic.drop(index=0)
    with pytest.raises(ValueError, match="168 hourly observations"):
        features.build_weather_features(generic, ecmwf, date(2026, 1, 8))


def test_weather_rejects_mismatched_location_sets(hourly_data):
    generic, ecmwf = hourly_data
    ecmwf["location_id"] = "DIFFERENT"
    with pytest.raises(ValueError, match="location sets do not match"):
        features.build_weather_features(generic, ecmwf, date(2026, 1, 8))


def test_weather_rejects_missing_required_column(hourly_data):
    generic, ecmwf = hourly_data
    with pytest.raises(ValueError, match="missing columns"):
        features.build_weather_features(
            generic.drop(columns="precipitation"), ecmwf, date(2026, 1, 8)
        )


def test_weather_rejects_missing_numeric_value(hourly_data):
    generic, ecmwf = hourly_data
    generic.loc[0, "precipitation"] = np.nan
    with pytest.raises(ValueError, match="missing values"):
        features.build_weather_features(generic, ecmwf, date(2026, 1, 8))


def test_weather_rejects_nonfinite_value(hourly_data):
    generic, ecmwf = hourly_data
    ecmwf.loc[0, "temperature_2m"] = np.inf
    with pytest.raises(ValueError, match="non-finite"):
        features.build_weather_features(generic, ecmwf, date(2026, 1, 8))


def test_weather_rejects_duplicate_hour_even_when_row_count_matches(hourly_data):
    # Quality edge case: 168 rows need not mean 168 distinct hours.
    generic, ecmwf = hourly_data
    generic.loc[1, "local_time"] = generic.loc[0, "local_time"]
    with pytest.raises(ValueError, match="duplicate|timestamp|hour"):
        features.build_weather_features(generic, ecmwf, date(2026, 1, 8))


def test_model_feature_assembly_matches_frozen_schema(hourly_data):
    generic, ecmwf = hourly_data
    weather = features.build_weather_features(generic, ecmwf, date(2026, 1, 8))
    static = pd.DataFrame([{
        "location_id": "TEST_001", "elevation_m": 1700.0,
        "slope_deg": 5.0, "flood_hazard_score": 2.0,
        "ghsl_built_up_fraction": 0.25,
    }])
    result = features.assemble_model_features(weather, static)
    assert len(features.PREDICTOR_COLUMNS) == 15
    assert list(result.columns) == [
        "location_id", "prediction_date", *features.PREDICTOR_COLUMNS
    ]
    assert not result[features.PREDICTOR_COLUMNS].isna().any().any()


def test_assembly_rejects_unmatched_static_location(hourly_data):
    generic, ecmwf = hourly_data
    weather = features.build_weather_features(generic, ecmwf, date(2026, 1, 8))
    static = pd.DataFrame([{
        "location_id": "OTHER", "elevation_m": 1700.0,
        "slope_deg": 5.0, "flood_hazard_score": 2.0,
        "ghsl_built_up_fraction": 0.25,
    }])
    with pytest.raises(ValueError, match="missing static"):
        features.assemble_model_features(weather, static)


# Historical ETL validators

def test_label_validator_accepts_valid_record(labels):
    assert "1 flood-label rows valid" in validation.validate_labels(labels)


@pytest.mark.parametrize("field,value", [
    ("verification_status", "unknown"),
    ("latitude", 100.0),
    ("longitude", 200.0),
    ("end_date", "2025-12-31"),
    ("flood_label", 0),
])
def test_label_validator_rejects_invalid_record(labels, field, value):
    labels.loc[0, field] = value
    with pytest.raises(ValueError):
        validation.validate_labels(labels)


def test_label_validator_rejects_duplicate_id(labels):
    duplicate = pd.concat([labels, labels], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate"):
        validation.validate_labels(duplicate)


def test_label_validator_rejects_missing_flood_label(labels):
    labels.loc[0, "flood_label"] = np.nan
    with pytest.raises(ValueError):
        validation.validate_labels(labels)


def test_chirps_validator_accepts_complete_window(chirps_daily, labels):
    assert "7 CHIRPS daily rows valid" in validation.validate_chirps_daily(chirps_daily, labels)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "negative"])
def test_chirps_validator_rejects_bad_window(chirps_daily, labels, defect):
    if defect == "missing":
        chirps_daily = chirps_daily.iloc[:-1]
    elif defect == "duplicate":
        chirps_daily.loc[0, "days_before_target"] = 2
    else:
        chirps_daily.loc[0, "rainfall_mm"] = -1
    with pytest.raises(ValueError):
        validation.validate_chirps_daily(chirps_daily, labels)


def test_era5_validator_accepts_complete_day(era5_hourly, labels):
    assert "24 ERA5 hourly rows valid" in validation.validate_era5_hourly(era5_hourly, labels)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "nan"])
def test_era5_validator_rejects_bad_hourly(era5_hourly, labels, defect):
    if defect == "missing":
        era5_hourly = era5_hourly.iloc[:-1]
    elif defect == "duplicate":
        era5_hourly.loc[1, "timestamp"] = era5_hourly.loc[0, "timestamp"]
    else:
        era5_hourly.loc[0, "temperature_c"] = np.nan
    with pytest.raises(ValueError):
        validation.validate_era5_hourly(era5_hourly, labels)

def test_era5_validator_rejects_infinite_measurement(era5_hourly, labels):
    era5_hourly.loc[0, "temperature_c"] = np.inf
    with pytest.raises(ValueError):
        validation.validate_era5_hourly(era5_hourly, labels)


# Alert content: no DB writes; persistence lifecycle is covered by TC-013.
@pytest.mark.parametrize("level,alert_level,active", [
    ("LOW", "LOW", False),
    ("moderate", "ADVISORY", True),
    ("HIGH", "WARNING", True),
])
def test_alert_content(level, alert_level, active):
    result = alerts.build_alert_content("Nairobi West", level, 0.65)
    assert result["alert_level"] == alert_level
    assert result["active"] is active
    assert "Nairobi West" in result["title"]
    assert result["instructions"]

def test_alert_rejects_unknown_risk_level():
    with pytest.raises(ValueError, match="Unsupported"):
        alerts.build_alert_content("Nairobi West", "CRITICAL", 0.7)

def test_alert_rejects_nonfinite_risk_score():
    with pytest.raises(ValueError):
        alerts.build_alert_content("Nairobi West", "MODERATE", float("nan"))
