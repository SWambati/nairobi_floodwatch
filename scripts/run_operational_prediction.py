import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.paths import PROCESSED_DIR
from src.acquisition.open_meteo import (OpenMeteoClient, save_open_meteo_snapshot)
from src.model.inference import FloodInferenceEngine
from src.model.risk_classification import FloodRiskClassifier
from src.operational.alerts import persist_alerts
from src.operational.persistence import (create_database_engine, ensure_data_source, ensure_production_model, load_operational_locations, load_static_features, persist_current_weather, persist_predictions)
from src.operational.runtime_features import (assemble_model_features, build_weather_features)


# Configuration
NAIROBI_TIMEZONE = "Africa/Nairobi"

# Fetch locations in batches rather than sending all 237 coordinates through one large Open-Meteo request.
API_BATCH_SIZE = 40

PREDICTION_OUTPUT_DIR = (PROCESSED_DIR / "operational" / "predictions")

def main():
    started = time.perf_counter()

    print("Nairobi FloodWatch Operational Prediction Pipeline")
    print("=" * 50)

    # Use Nairobi's local date when constructing D-1, D-3 and D-7 antecedent weather feature windows.
    prediction_date = datetime.now(ZoneInfo(NAIROBI_TIMEZONE)).date()

    # Store prediction timestamps in UTC. PostgreSQL timestamptz handles timezone conversion when the records are later queried.
    prediction_time = datetime.now(timezone.utc)

    # Connect to the PostgreSQL database.
    db_engine = create_database_engine()

    # Load all monitored Nairobi observation points. Their coordinates are passed to Open-Meteo for near-real-time weather acquisition.
    print("Loading operational locations from PostgreSQL...")

    locations = load_operational_locations(db_engine)

    print(f"Operational locations: {len(locations):,}")
    print()

    # Retrieve seven historical days plus the current/forecast day from Open-Meteo for every operational observation point.
    print("Fetching Open-Meteo data...")

    client = OpenMeteoClient()
    weather = client.fetch_locations_batched(locations, batch_size=API_BATCH_SIZE)

    # Preserve timestamped API snapshots for provenance and debugging.
    snapshot_files = save_open_meteo_snapshot(weather)

    print(f"Generic hourly rows: {len(weather['generic_hourly']):,}")
    print(f"ECMWF hourly rows: {len(weather['ecmwf_hourly']):,}")
    print(f"Current rows: {len(weather['current']):,}")
    print()

    # Convert Open-Meteo hourly observations into the same 11 weather predictors used during historical model training.
    print("Generating operational weather features...")

    weather_features = build_weather_features(weather["generic_hourly"], weather["ecmwf_hourly"], prediction_date=prediction_date)

    # Load elevation, slope, flood-hazard score and the latest valid GHSL built-up fraction from PostgreSQL.
    static_features = load_static_features(db_engine, prediction_date)

    # Combine 11 runtime weather features and 4 static environmental features into the frozen 15-column Random Forest schema.
    model_features = assemble_model_features( weather_features, static_features)

    if len(model_features) != len(locations):
        raise ValueError("Operational feature row count does not match the PostgreSQL location count.")

    print(f"Operational model rows: {len(model_features):,}")
    print("Predictor count: 15")
    print()

    # Load the exact persisted production Random Forest validated during the implementation of prediction inference and generate a flood-risk score for each observation point.
    print("Running production Random Forest inference...")

    inference_engine = FloodInferenceEngine()

    # The inference engine returns prediction outputs but does not retain all 15 predictor columns required for database auditing.
    inference_results = inference_engine.predict_batch(model_features)

    if len(inference_results) != len(model_features):
        raise ValueError("Inference result count does not match operational feature rows.")

    # Preserve the exact 15-feature input row and append model outputs.
    # The prediction_data JSONB column will later contain the complete feature snapshot used to generate each operational prediction.
    predictions = model_features.reset_index(drop=True).copy()
    inference_results = inference_results.reset_index(drop=True)

    inference_columns = [
        "flood_probability",
        "predicted_label",
        "decision_threshold",
        "model_name",
        "model_version",
    ]

    missing_inference_columns = (set(inference_columns) - set(inference_results.columns))

    if missing_inference_columns:
        raise ValueError(f"Inference output is missing columns: {sorted(missing_inference_columns)}")

    for column in inference_columns:
        predictions[column] = inference_results[column].to_numpy()

    # Convert RF scores into the Task 27 LOW, MODERATE and HIGH operational risk bands.
    risk_classifier = FloodRiskClassifier()
    predictions = risk_classifier.classify_batch(predictions)

    # Add location names and coordinates for reporting and dashboard use.
    # These metadata fields are never passed into the Random Forest.
    predictions = predictions.merge(
        locations[
            [
                "location_id",
                "name",
                "latitude",
                "longitude",
            ]
        ],
        on="location_id",
        how="left",
        validate="one_to_one",
    )

    predictions = predictions.rename(columns={"name": "location_name",})

    # Register Open-Meteo sources and the deployed model in PostgreSQL so stored observations and predictions retain source provenance.
    print("Registering operational database metadata...")

    best_match_source_id = ensure_data_source(
        db_engine,
        name="Open-Meteo Best Match",
        source_type="near_real_time_weather",
        source_url="https://api.open-meteo.com/v1/forecast",
        update_frequency="hourly",
    )

    ensure_data_source(
        db_engine,
        name="Open-Meteo ECMWF IFS",
        source_type="near_real_time_weather",
        source_url="https://api.open-meteo.com/v1/ecmwf",
        update_frequency="hourly",
    )

    model_id = ensure_production_model(db_engine, model_name=inference_engine.model_name, model_version=inference_engine.model_version)

    print(f"Model database ID: {model_id}")
    print()

    # Store the latest environmental conditions for later dashboard use.
    # weather_data has a unique location/source/timestamp constraint, so rerunning the same observation updates rather than duplicates it.
    print("Persisting operational weather...")

    weather_rows = persist_current_weather(db_engine, weather["current"], best_match_source_id)

    print(f"Weather rows written/upserted: {weather_rows:,}")

    # Store the RF risk score, risk category, model version and exact 15-feature snapshot used to produce each prediction.
    print("Persisting flood-risk predictions...")

    prediction_ids = persist_predictions(db_engine, predictions, model_id, prediction_time)

    print(f"Prediction rows written/updated: {len(prediction_ids):,}")
    print()

    # Generate operational advisories for MODERATE risk and warnings for HIGH risk. LOW predictions do not create active alerts.
    print("Generating flood advisories and alerts...")

    alert_summary = persist_alerts(db_engine, predictions, prediction_ids, issued_at=prediction_time)

    print(f"New alerts created: {alert_summary['created']:,}")
    print(f"Existing alerts updated: {alert_summary['updated']:,}")
    print(f"Alerts resolved: {alert_summary['resolved']:,}")
    print(f"Active alerts: {alert_summary['active']:,}")
    print(f"Expired alerts: {alert_summary['expired']:,}")
    print(f"Superseded alerts: {alert_summary['superseded']:,}")
    print()

    # Save a CSV snapshot of the operational run for debugging, evaluation and later pipeline-performance reporting.
    PREDICTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = (PREDICTION_OUTPUT_DIR / f"operational_predictions_{prediction_date.isoformat()}.csv")
    predictions.to_csv(output_file, index=False)

    # Summarize the current point-level Nairobi risk distribution.
    counts = predictions["risk_level"].value_counts()

    low = int(counts.get("LOW", 0))
    moderate = int(counts.get("MODERATE", 0))
    high = int(counts.get("HIGH", 0))

    # Every observation point must receive exactly one risk category.
    if low + moderate + high != len(predictions):
        raise ValueError("Risk counts do not match prediction rows.")

    # Measure the entire workflow, including prediction persistence and alert generation.
    elapsed = (time.perf_counter() - started)

    print("Operational risk distribution:")
    print(f"LOW: {low:,}")
    print(f"MODERATE: {moderate:,}")
    print(f"HIGH: {high:,}")
    print()

    print(f"Model: {inference_engine.model_name}")
    print(f"Model version: {inference_engine.model_version}")
    print(f"Prediction date: {prediction_date}")
    print(f"Total pipeline time: {elapsed:.2f} seconds")
    print()

    print(f"Saved predictions: {output_file}")
    print(f"Saved Open-Meteo snapshots: {snapshot_files['metadata']}")
    print()

    print("Operational prediction pipeline: OK")


if __name__ == "__main__":
    main()