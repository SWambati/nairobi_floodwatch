import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.paths import PROCESSED_DIR
from src.acquisition.open_meteo import OpenMeteoClient, save_open_meteo_snapshot
from src.model.inference import FloodInferenceEngine
from src.model.risk_classification import FloodRiskClassifier
from src.operational.alerts import persist_alerts
from src.operational.monitoring import OperationalMonitor
from src.operational.persistence import (
    create_database_engine,
    ensure_data_source,
    ensure_production_model,
    load_operational_locations,
    load_static_features,
    persist_current_weather,
    persist_predictions,
)
from src.operational.runtime_features import assemble_model_features, build_weather_features

NAIROBI_TIMEZONE = "Africa/Nairobi"
API_BATCH_SIZE = 40
PREDICTION_OUTPUT_DIR = PROCESSED_DIR / "operational" / "predictions"

INFERENCE_COLUMNS = (
    "flood_probability",
    "predicted_label",
    "decision_threshold",
    "model_name",
    "model_version",
)


def main():
    started = time.perf_counter()
    prediction_date = datetime.now(ZoneInfo(NAIROBI_TIMEZONE)).date()
    prediction_time = datetime.now(timezone.utc)

    print("Nairobi FloodWatch Operational Prediction Pipeline")
    print("=" * 50)

    # Local monitoring records connection failures even before PostgreSQL is available.
    with OperationalMonitor(job_name="operational_prediction") as monitor:

        with monitor.stage("database_connection"):
            db_engine = create_database_engine()

        monitor.metric("monitoring_db_attached", int(monitor.attach_engine(db_engine)))

        with monitor.stage("load_locations"):
            print("Loading operational locations from PostgreSQL...")
            locations = load_operational_locations(db_engine)
            monitor.metric("locations_loaded", len(locations))
            print(f"Operational locations: {len(locations):,}\n")

        with monitor.stage("open_meteo_acquisition"):
            print("Fetching Open-Meteo data...")
            client = OpenMeteoClient()
            weather = client.fetch_locations_batched(locations, batch_size=API_BATCH_SIZE)

            monitor.metric(
                "weather_batches_planned",
                (len(locations) + API_BATCH_SIZE - 1) // API_BATCH_SIZE,
            )
            monitor.metric("generic_hourly_rows", len(weather["generic_hourly"]))
            monitor.metric("ecmwf_hourly_rows", len(weather["ecmwf_hourly"]))
            monitor.metric("current_weather_rows", len(weather["current"]))

            print(f"Generic hourly rows: {len(weather['generic_hourly']):,}")
            print(f"ECMWF hourly rows: {len(weather['ecmwf_hourly']):,}")
            print(f"Current rows: {len(weather['current']):,}\n")

        with monitor.stage("weather_snapshot"):
            snapshot_files = save_open_meteo_snapshot(weather)

        with monitor.stage("weather_feature_generation"):
            print("Generating operational weather features...")
            weather_features = build_weather_features(
                weather["generic_hourly"],
                weather["ecmwf_hourly"],
                prediction_date=prediction_date,
            )
            monitor.metric("weather_feature_rows", len(weather_features))

        with monitor.stage("static_feature_loading"):
            static_features = load_static_features(db_engine, prediction_date)
            monitor.metric("static_feature_rows", len(static_features))

        with monitor.stage("model_feature_assembly"):
            model_features = assemble_model_features(weather_features, static_features)

            if len(model_features) != len(locations):
                raise ValueError(
                    "Operational feature row count does not match the PostgreSQL location count."
                )

            monitor.metric("model_feature_rows", len(model_features))

            print(f"Operational model rows: {len(model_features):,}")
            print("Predictor count: 15\n")

        with monitor.stage("model_inference"):
            print("Running production Random Forest inference...")
            inference_engine = FloodInferenceEngine()
            inference_results = inference_engine.predict_batch(model_features)

            if len(inference_results) != len(model_features):
                raise ValueError(
                    "Inference result count does not match operational feature rows."
                )

            missing = set(INFERENCE_COLUMNS) - set(inference_results.columns)
            if missing:
                raise ValueError(
                    f"Inference output is missing columns: {sorted(missing)}"
                )

            predictions = model_features.reset_index(drop=True).copy()
            inference_results = inference_results.reset_index(drop=True)

            for column in INFERENCE_COLUMNS:
                predictions[column] = inference_results[column].to_numpy()

            monitor.metric("predictions_generated", len(predictions))

        with monitor.stage("risk_classification"):
            predictions = FloodRiskClassifier().classify_batch(predictions)

            counts = predictions["risk_level"].value_counts()
            low = int(counts.get("LOW", 0))
            moderate = int(counts.get("MODERATE", 0))
            high = int(counts.get("HIGH", 0))

            # Validate classifications before storing predictions or generating alerts.
            if low + moderate + high != len(predictions):
                raise ValueError("Risk counts do not match prediction rows.")

            monitor.metric("risk_low_count", low)
            monitor.metric("risk_moderate_count", moderate)
            monitor.metric("risk_high_count", high)

        with monitor.stage("prediction_enrichment"):
            predictions = predictions.merge(
                locations[["location_id", "name", "latitude", "longitude"]],
                on="location_id",
                how="left",
                validate="one_to_one",
            ).rename(columns={"name": "location_name"})

        with monitor.stage("register_metadata"):
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

            model_id = ensure_production_model(
                db_engine,
                model_name=inference_engine.model_name,
                model_version=inference_engine.model_version,
            )

            print(f"Model database ID: {model_id}\n")

        with monitor.stage("weather_persistence"):
            print("Persisting operational weather...")

            weather_rows = persist_current_weather(
                db_engine,
                weather["current"],
                best_match_source_id,
            )

            monitor.metric("weather_rows_written", int(weather_rows))
            print(f"Weather rows written/upserted: {weather_rows:,}")

        with monitor.stage("prediction_persistence"):
            print("Persisting flood-risk predictions...")

            prediction_ids = persist_predictions(
                db_engine,
                predictions,
                model_id,
                prediction_time,
            )

            monitor.metric("prediction_rows_written", len(prediction_ids))
            print(f"Prediction rows written/updated: {len(prediction_ids):,}\n")

        with monitor.stage("alert_generation"):
            print("Generating flood advisories and alerts...")

            alert_summary = persist_alerts(
                db_engine,
                predictions,
                prediction_ids,
                issued_at=prediction_time,
            )

            for key in (
                "created",
                "updated",
                "resolved",
                "active",
                "expired",
                "superseded",
            ):
                monitor.metric(f"alerts_{key}", int(alert_summary[key]))
                print(f"Alerts {key}: {alert_summary[key]:,}")

            print()

        with monitor.stage("csv_snapshot"):
            PREDICTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            output_file = (
                PREDICTION_OUTPUT_DIR
                / f"operational_predictions_{prediction_date.isoformat()}.csv"
            )

            predictions.to_csv(output_file, index=False)

        elapsed = time.perf_counter() - started

        print("Operational risk distribution:")
        print(f"LOW: {low:,}")
        print(f"MODERATE: {moderate:,}")
        print(f"HIGH: {high:,}\n")

        print(f"Model: {inference_engine.model_name}")
        print(f"Model version: {inference_engine.model_version}")
        print(f"Prediction date: {prediction_date}")
        print(f"Total pipeline time: {elapsed:.2f} seconds\n")

        print(f"Saved predictions: {output_file}")
        print(f"Saved Open-Meteo snapshots: {snapshot_files['metadata']}\n")
        print("Operational prediction pipeline: OK")


if __name__ == "__main__":
    main()