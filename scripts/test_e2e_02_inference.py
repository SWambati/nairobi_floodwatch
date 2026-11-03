import math
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

from src.acquisition.open_meteo import OpenMeteoClient
from src.operational.persistence import (create_database_engine, load_operational_locations, load_static_features)
from src.operational.runtime_features import (build_weather_features, assemble_model_features)
from src.model.inference import FloodInferenceEngine
from src.model.risk_classification import FloodRiskClassifier

NAIROBI_TZ = ZoneInfo("Africa/Nairobi")
API_BATCH_SIZE = 40

REQUIRED_INFERENCE_COLUMNS = ("flood_probability", "predicted_label", "decision_threshold", "model_name", "model_version")
VALID_RISK_LEVELS = {"LOW", "MODERATE", "HIGH"}

def log(message):
    print(message, flush=True)

def check(condition, description):
    if not condition:
        raise AssertionError(description)
    log(f"[PASS] {description}")

def check_location_ids(frame, expected_ids, label):
    check(isinstance(frame, pd.DataFrame), f"{label} is a DataFrame")
    check("location_id" in frame.columns, f"{label} contains location_id")

    identifiers = frame["location_id"].astype(str)

    check(not identifiers.duplicated().any(), f"{label} contains no duplicate monitoring points")
    check(set(identifiers) == expected_ids, f"{label} covers all expected locations")

def check_numeric_column(frame, column):
    check(column in frame.columns, f"{column} column exists")
    values = pd.to_numeric(frame[column], errors="coerce")
    check(values.notna().all() and values.map(math.isfinite).all(), f"{column} contains valid finite values")
    return values

def main():
    started = time.perf_counter()
    durations = {}
    prediction_date = datetime.now(NAIROBI_TZ).date()

    log("=" * 50)
    log("NAIROBI FLOODWATCH — E2E-02")
    log("=" * 50)
    log(f"Prediction date: {prediction_date}")
    log("Mode: READ-ONLY INFERENCE")
    log("")

    # Operational locations
    stage_start = time.perf_counter()
    engine = create_database_engine()
    locations = load_operational_locations(engine)

    check(isinstance(locations, pd.DataFrame) and not locations.empty, "Operational locations loaded successfully")

    expected_count = len(locations)
    expected_ids = set(locations["location_id"].astype(str))

    check(len(expected_ids) == expected_count, "All monitoring-location IDs are unique")
    durations["Location loading"] = (time.perf_counter() - stage_start)
    log(f"Monitoring locations: {expected_count}")

    #Open-Meteo acquisition
    log("\n--- WEATHER ACQUISITION ---")

    stage_start = time.perf_counter()
    client = OpenMeteoClient()
    weather = client.fetch_locations_batched(locations, batch_size=API_BATCH_SIZE)

    check(isinstance(weather, dict) and all(key in weather for key in ("generic_hourly", "ecmwf_hourly", "current")), "Both hourly weather sources were acquired")

    durations["Weather acquisition"] = (time.perf_counter() - stage_start)

    log("Best Match hourly rows: " f"{len(weather['generic_hourly'])}")
    log("ECMWF hourly rows: " f"{len(weather['ecmwf_hourly'])}")

    #Weather feature generation
    log("\n--- WEATHER FEATURE GENERATION ---")
    stage_start = time.perf_counter()
    weather_features = build_weather_features(weather["generic_hourly"], weather["ecmwf_hourly"], prediction_date=prediction_date)

    check(len(weather_features) == expected_count, "Weather features match location count")
    check_location_ids(weather_features, expected_ids, "Weather features")

    for column in ("rain_1d", "rain_3d", "rain_7d"):
        values = check_numeric_column(weather_features, column)
        check((values >= 0).all(), f"{column} contains nonnegative rainfall")

    durations["Weather features"] = (time.perf_counter() - stage_start)

    #Static features from PostgreSQL
    log("\n--- STATIC FEATURE RETRIEVAL ---")
    stage_start = time.perf_counter()
    static_features = load_static_features(engine, prediction_date)

    check(isinstance(static_features, pd.DataFrame) and not static_features.empty, "Static features retrieved from PostgreSQL")
    check_location_ids(static_features, expected_ids, "Static features")
    durations["Static features"] = (time.perf_counter() - stage_start)

    #Complete model feature assembly
    log("\n--- MODEL FEATURE ASSEMBLY ---")
    stage_start = time.perf_counter()
    model_features = assemble_model_features(weather_features, static_features)

    check(isinstance(model_features, pd.DataFrame), "Model feature assembly returned a DataFrame")
    check(len(model_features) == expected_count, "Model feature row count matches locations")
    check_location_ids(model_features, expected_ids, "Assembled model features")

    durations["Feature assembly"] = (time.perf_counter() - stage_start)
    log(f"Assembled rows: {len(model_features)}")
    log(f"Available columns: {len(model_features.columns)}")

    #The inference engine validates the exact predictor schema required by the trained model artifact.
    #Production Random Forest inference
    log("\n--- RANDOM FOREST INFERENCE ---")
    stage_start = time.perf_counter()
    inference_engine = FloodInferenceEngine()
    results = inference_engine.predict_batch(model_features)
    check(isinstance(results, pd.DataFrame), "Inference engine returned a DataFrame")
    check(len(results) == expected_count, "Exactly one inference result per location")

    missing = set(REQUIRED_INFERENCE_COLUMNS) - set(results.columns)
    check(not missing, "All required inference outputs exist")

    probabilities = check_numeric_column(results, "flood_probability")
    check(probabilities.between(0, 1).all(), "Flood probabilities are between 0 and 1")

    thresholds = check_numeric_column(results, "decision_threshold")
    check(thresholds.between(0, 1).all(), "Decision thresholds are within valid bounds")

    check(results["predicted_label"].notna().all(), "No predicted labels are missing")
    check(results["model_name"].notna().all() and results["model_version"].notna().all(), "Model identity and version are recorded")

    durations["Model inference"] = (time.perf_counter() - stage_start)

    #Risk classification
    log("\n--- RISK CLASSIFICATION ---")
    stage_start = time.perf_counter()
    predictions = model_features.reset_index(drop=True).copy()
    results = results.reset_index(drop=True)

    for column in REQUIRED_INFERENCE_COLUMNS:
        predictions[column] = (results[column].to_numpy())

    classifier = FloodRiskClassifier()
    predictions = classifier.classify_batch(predictions)

    check(len(predictions) == expected_count, "Risk classification preserved all predictions")
    check_location_ids(predictions, expected_ids,"Classified predictions")
    check("risk_level" in predictions.columns, "Risk classification column exists")
    check(predictions["risk_level"].isin(VALID_RISK_LEVELS).all(), "Every prediction has a valid risk level")
    
    scores = check_numeric_column(predictions, "flood_probability")
    check(scores.between(0, 1).all(), "All model scores are within valid bounds")
    
    # Ensure the risk classifier has not modified the Random Forest's original numeric outputs.
    check(all(math.isclose(float(before), float(after), rel_tol=0.0, abs_tol=1e-12) for before, after in zip(probabilities, scores)), "Risk classification preserved original model scores")

    counts = predictions["risk_level"].value_counts()

    low = int(counts.get("LOW", 0))
    moderate = int(counts.get("MODERATE", 0))
    high = int(counts.get("HIGH", 0))
    check(low + moderate + high == expected_count, "Risk distribution totals match location count")

    durations["Risk classification"] = (time.perf_counter() - stage_start)

    #RESULT SUMMARY
    elapsed = time.perf_counter() - started

    log("\n" + "=" * 50)
    log("E2E-02 RESULT: PASS")
    log("=" * 50)

    log(f"Model: {inference_engine.model_name}")
    log(f"Version: {inference_engine.model_version}")
    log(f"Assessment date: {prediction_date}")

    log(f"\nLocations processed: {expected_count}")
    log(f"Predictions generated: {len(results)}")

    log("\nRisk classifications:")
    log(f"LOW: {low}")
    log(f"MODERATE: {moderate}")
    log(f"HIGH: {high}")
    log("\nStage durations:")

    for name, duration in durations.items():
        log(f"{name}: {duration:.2f} seconds")

    log(f"\nTotal test time: {elapsed:.2f} seconds")
    log("\nREAD-ONLY TEST: No predictions, weather records or alerts were persisted.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-02 RESULT: FAIL\n" f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
