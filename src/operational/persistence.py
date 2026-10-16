import json
import os
from datetime import date, datetime
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from src.model.model_dataset import PREDICTOR_COLUMNS
from config.settings import get_settings


# Configuration
NAIROBI_TIMEZONE = "Africa/Nairobi"

#static spatial and environmental features stored in PostgreSQL
STATIC_FEATURES = ["elevation_m", "slope_deg", "flood_hazard_score", "ghsl_built_up_fraction"]


# Database connection
def create_database_engine() -> Engine:
    #load PostgreSQL config from the settings module to support local .env files and deployment environment variables

    settings = get_settings()

    engine = create_engine(settings.database_url, pool_pre_ping=True)

    #fail immediately if PostgreSQL is not reachable
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return engine


# Operational locations
def load_operational_locations(engine: Engine) -> pd.DataFrame:
    #load observation points used for flood predictions for OpenMeteo acquisition and location data persistence
    query = text("""
        SELECT
            location_id,
            name,
            administrative_level,
            latitude,
            longitude
        FROM location
        WHERE administrative_level = 'observation_point'
        ORDER BY location_id
    """)

    with engine.connect() as connection:
        locations = pd.read_sql(query, connection)

    if locations.empty:
        raise ValueError("No operational observation points were found in PostgreSQL.")

    if locations["location_id"].duplicated().any():
        raise ValueError("Operational locations contain duplicate location IDs.")

    #Join Location IDs as strings with OpenMeteo and environmental data for runtime feature modules
    locations["location_id"] = (locations["location_id"].astype(int).astype(str))
    locations["latitude"] = pd.to_numeric(locations["latitude"], errors="raise")
    locations["longitude"] = pd.to_numeric(locations["longitude"], errors="raise")

    return locations


# Static environmental features
def load_static_features(engine: Engine, prediction_date: date) -> pd.DataFrame:
    #Load the static predictors required by the production Random Forest Classifier. Select the latest date after the prediction date for each location and feature.
    query = text("""
        WITH ranked_features AS (
            SELECT
                location_id,
                feature_name,
                feature_value,
                effective_date,
                ROW_NUMBER() OVER (
                    PARTITION BY location_id, feature_name
                    ORDER BY
                        effective_date DESC NULLS LAST,
                        feature_id DESC
                ) AS row_number
            FROM environmental_feature
            WHERE feature_name = ANY(:feature_names)
              AND (
                    effective_date IS NULL
                    OR effective_date <= :prediction_date
              )
        )
        SELECT
            location_id,
            feature_name,
            feature_value,
            effective_date
        FROM ranked_features
        WHERE row_number = 1
        ORDER BY location_id, feature_name
    """)

    with engine.connect() as connection:
        raw = pd.read_sql(
            query,
            connection,
            params={
                "feature_names": STATIC_FEATURES,
                "prediction_date": prediction_date,
            },
        )

    if raw.empty:
        raise ValueError("No operational environmental features were found.")

    raw["feature_value"] = pd.to_numeric(raw["feature_value"], errors="raise")

    #pivot environmental_features table to store features into one model-ready row for each location
    static = (raw.pivot(index="location_id", columns="feature_name", values="feature_value").reset_index())
    static.columns.name = None
    static["location_id"] = (static["location_id"].astype(int).astype(str))

    missing_columns = set(STATIC_FEATURES) - set(static.columns)

    if missing_columns:
        raise ValueError(f"Static feature query is missing: {sorted(missing_columns)}")

    if static[STATIC_FEATURES].isna().any().any():
        raise ValueError("One or more operational locations are missing static features.")

    return static[["location_id", *STATIC_FEATURES]]


# Data-source registration
def ensure_data_source(engine: Engine, name: str, source_type: str, source_url: str, update_frequency: str) -> int:
    #Return source_id for operational data sources. Existing sources are updated while sources that do not exist in data_source table are inserted
    with engine.begin() as connection:
        source_id = connection.execute(
            text("""
                SELECT source_id
                FROM data_source
                WHERE name = :name
                ORDER BY source_id
                LIMIT 1
            """),
            {"name": name},
        ).scalar()

        if source_id is not None:
            connection.execute(
                text("""
                    UPDATE data_source
                    SET
                        source_type = :source_type,
                        source_url = :source_url,
                        update_frequency = :update_frequency,
                        is_active = TRUE
                    WHERE source_id = :source_id
                """),
                {
                    "source_id": source_id,
                    "source_type": source_type,
                    "source_url": source_url,
                    "update_frequency": update_frequency,
                },
            )

            return int(source_id)

        source_id = connection.execute(
            text("""
                INSERT INTO data_source (
                    name,
                    source_type,
                    description,
                    source_url,
                    update_frequency,
                    is_active,
                    created_at
                )
                VALUES (
                    :name,
                    :source_type,
                    :description,
                    :source_url,
                    :update_frequency,
                    TRUE,
                    NOW()
                )
                RETURNING source_id
            """),
            {
                "name": name,
                "source_type": source_type,
                "description": ("Near-real-time operational weather source for Nairobi FloodWatch."),
                "source_url": source_url,
                "update_frequency": update_frequency,
            },
        ).scalar_one()

    return int(source_id)


# Production model registration
def ensure_production_model(engine: Engine, model_name: str, model_version: str) -> int:
    #Ensure deployed RF is persisted in the model table. Persisted model SHA-derived version from FloodInferenceEngine is used for traceability of predictions
    with engine.begin() as connection:
        model_id = connection.execute(
            text("""
                SELECT model_id
                FROM model
                WHERE name = :name
                  AND version = :version
                ORDER BY model_id
                LIMIT 1
            """),
            {
                "name": model_name,
                "version": model_version,
            },
        ).scalar()

        if model_id is not None:
            connection.execute(
                text("""
                    UPDATE model
                    SET is_active = TRUE
                    WHERE model_id = :model_id
                """),
                {"model_id": model_id},
            )

            return int(model_id)

        # Older versions of this production model are retained for traceability but marked inactive.
        connection.execute(
            text("""
                UPDATE model
                SET is_active = FALSE
                WHERE name = :name
            """),
            {"name": model_name},
        )

        model_id = connection.execute(
            text("""
                INSERT INTO model (
                    name,
                    model_type,
                    description,
                    version,
                    is_active,
                    created_at
                )
                VALUES (
                    :name,
                    'RandomForestClassifier',
                    :description,
                    :version,
                    TRUE,
                    NOW()
                )
                RETURNING model_id
            """),
            {
                "name": model_name,
                "version": model_version,
                "description": (
                    "Final production Nairobi FloodWatch Random Forest "
                    "classifier using the frozen 15-feature schema."
                ),
            },
        ).scalar_one()

    return int(model_id)


# Weather persistence
def persist_current_weather(engine: Engine, current: pd.DataFrame, source_id: int) -> int:
    #store latest Open_meteo conditions for each observation point
    #Since PostgreSQL has a unique constraint on location_id + source_id + timestamp, rerunning the same observation updates the existing record instead of creating a duplicate.
    frame = current.copy()

    timestamps = pd.to_datetime(frame["local_time"], errors="raise")

    # Open-Meteo returns Nairobi-local timestamps without an explicit UTC offset, so attach Africa/Nairobi before inserting into timestamptz.
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize(NAIROBI_TIMEZONE)

    else:
        timestamps = timestamps.dt.tz_convert(NAIROBI_TIMEZONE)

    frame["timestamp"] = timestamps

    records = []

    for row in frame.itertuples(index=False):
        records.append({
            "location_id": int(row.location_id),
            "source_id": source_id,
            "timestamp": row.timestamp.to_pydatetime(),
            "temperature": float(row.temperature_2m),
            "humidity": float(row.relative_humidity_2m),
            "rainfall": float(row.precipitation),
            "pressure": float(row.surface_pressure),
        })

    statement = text("""
        INSERT INTO weather_data (
            location_id,
            source_id,
            timestamp,
            temperature,
            humidity,
            rainfall,
            pressure,
            created_at
        )
        VALUES (
            :location_id,
            :source_id,
            :timestamp,
            :temperature,
            :humidity,
            :rainfall,
            :pressure,
            NOW()
        )
        ON CONFLICT (
            location_id,
            source_id,
            timestamp
        )
        DO UPDATE SET
            temperature = EXCLUDED.temperature,
            humidity = EXCLUDED.humidity,
            rainfall = EXCLUDED.rainfall,
            pressure = EXCLUDED.pressure
    """)

    with engine.begin() as connection:
        connection.execute(statement, records)

    return len(records)


# Prediction persistence
def persist_predictions(engine: Engine, predictions: pd.DataFrame, model_id: int, prediction_time: datetime) -> list[int]:
    #store the operational RF predictions in flood_risk_prediction.
    #Each prediction is maintained per location/model/day. If the operational pipeline is rerun on the same day, the existing row is refreshed instead of being duplicated
    #Prediction_data stores the complete feature snapshot and model provenance for reproducibility and auditing of every prediction
    prediction_ids = []

    with engine.begin() as connection:
        for _, row in predictions.iterrows():
            location_id = int(row["location_id"])
            prediction_date = pd.to_datetime(row["prediction_date"]).date()

            # Save the exact model input and threshold information alongside the prediction for reproducibility and later system auditing.
            payload = {
                "prediction_date": prediction_date.isoformat(),
                "model_name": str(row["model_name"]),
                "model_version": str(row["model_version"]),
                "binary_prediction": int(row["predicted_label"]),
                "binary_decision_threshold": float(row["decision_threshold"]),
                "risk_thresholds": {
                    "moderate": float(row["moderate_threshold"]),
                    "high": float(row["high_threshold"]),
                },
                "predictors": {
                    column: float(row[column])
                    for column in PREDICTOR_COLUMNS
                },
                "operational_sources": {
                    "rainfall": "Open-Meteo Best Match",
                    "meteorology": "Open-Meteo ECMWF IFS",
                },
            }

            # Check whether this location already has a prediction from this deployed model for the current Nairobi calendar date.
            existing_id = connection.execute(
                text("""
                    SELECT prediction_id
                    FROM flood_risk_prediction
                    WHERE location_id = :location_id
                      AND model_id = :model_id
                      AND (
                            prediction_time
                            AT TIME ZONE 'Africa/Nairobi'
                          )::date = :prediction_date
                    ORDER BY prediction_time DESC
                    LIMIT 1
                """),
                {
                    "location_id": location_id,
                    "model_id": model_id,
                    "prediction_date": prediction_date,
                },
            ).scalar()

            parameters = {
                "location_id": location_id,
                "model_id": model_id,
                "prediction_time": prediction_time,
                "risk_level": str(row["risk_level"]),
                "risk_score": float(row["flood_probability"]),
                "prediction_data": json.dumps(payload),
            }

            if existing_id is not None:
                # Same location + model + day: refresh the prediction.
                connection.execute(
                    text("""
                        UPDATE flood_risk_prediction
                        SET
                            prediction_time = :prediction_time,
                            risk_level = :risk_level,
                            risk_score = :risk_score,
                            prediction_data = CAST(
                                :prediction_data AS jsonb
                            ),
                            forecast_horizon_hours = NULL,
                            confidence_score = NULL
                        WHERE prediction_id = :prediction_id
                    """),
                    {
                        **parameters,
                        "prediction_id": int(existing_id),
                    },
                )

                prediction_ids.append(int(existing_id))

            else:
                # First operational prediction for this point/model/date.
                prediction_id = connection.execute(
                    text("""
                        INSERT INTO flood_risk_prediction (
                            location_id,
                            model_id,
                            prediction_time,
                            forecast_horizon_hours,
                            risk_level,
                            risk_score,
                            prediction_data,
                            confidence_score,
                            created_at
                        )
                        VALUES (
                            :location_id,
                            :model_id,
                            :prediction_time,
                            NULL,
                            :risk_level,
                            :risk_score,
                            CAST(:prediction_data AS jsonb),
                            NULL,
                            NOW()
                        )
                        RETURNING prediction_id
                    """),
                    parameters,
                ).scalar_one()

                prediction_ids.append(int(prediction_id))

    return prediction_ids