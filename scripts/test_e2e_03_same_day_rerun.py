from contextlib import nullcontext
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from sqlalchemy import text

from src.operational.persistence import (PREDICTOR_COLUMNS, create_database_engine, persist_predictions)
from src.operational.alerts import persist_alerts

NAIROBI_TZ = ZoneInfo("Africa/Nairobi")

class TransactionBoundEngine:
    #Direct production persistence calls through the test's uncommitted connection.
    #This prevents engine.begin() from committing changes.
  
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return nullcontext(self.connection)

def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}", flush=True)

def make_prediction(location_id, date, score):
    #Build a synthetic persistence payload.
    #Values are not intended to represent genuine weather conditions or model predictions.
 
    row = {name: 0.0 for name in PREDICTOR_COLUMNS}
    row.update({
        "location_id": location_id,
        "location_name": "FloodWatch E2E Test Point",
        "prediction_date": date.isoformat(),
        "model_name": "nairobi_floodwatch_random_forest",
        "model_version": "e2e-rollback-only",
        "flood_probability": score,
        "predicted_label": 0,
        "decision_threshold": 0.5,
        "moderate_threshold": 0.0999,
        "high_threshold": 0.6862,
        "risk_level": "MODERATE",
    })

    return pd.DataFrame([row])

def prediction_records(connection, location_id):
    return connection.execute(
        text("""
            SELECT
                prediction_id,
                prediction_time,
                risk_score,
                risk_level
            FROM flood_risk_prediction
            WHERE location_id = :location_id
            ORDER BY prediction_id
        """),
        {"location_id": location_id},
    ).mappings().all()

def alert_records(connection, prediction_id):
    return connection.execute(
        text("""
            SELECT
                alert_id,
                prediction_id,
                status,
                issued_at,
                expires_at
            FROM flood_alert
            WHERE prediction_id = :prediction_id
            ORDER BY alert_id
        """),
        {"prediction_id": prediction_id},
    ).mappings().all()

def database_counts(connection):
    return {
        table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in ("location", "flood_risk_prediction", "flood_alert")
    }

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-03B/C")
    print("=" * 50)
    print("Mode: ROLLBACK-ONLY DATABASE TEST")

    # Fixed historical times prevent the test from depending on the system clock or scheduler.
    morning = datetime(2025, 2, 12, 6, 0, tzinfo=NAIROBI_TZ).astimezone(timezone.utc)
    evening = datetime(2025, 2, 12, 18, 0, tzinfo=NAIROBI_TZ).astimezone(timezone.utc)
    assessment_date = morning.astimezone(NAIROBI_TZ).date()

    check(evening.astimezone(NAIROBI_TZ).date() == assessment_date, "Both executions belong to the same Nairobi date")
    check((evening - morning).total_seconds() == 43200, "Executions are exactly 12 hours apart")

    engine = create_database_engine()

    try:
        with engine.connect() as connection:
            baseline = database_counts(connection)
            transaction = connection.begin_nested()
            test_location_id = None

            try:
                model_id = connection.execute(
                    text("""
                        SELECT model_id
                        FROM model
                        WHERE name =
                            'nairobi_floodwatch_random_forest'
                          AND is_active = TRUE
                        ORDER BY model_id DESC
                        LIMIT 1
                    """)
                ).scalar_one_or_none()

                check(model_id is not None, "Active production model exists")

                test_location_id = connection.execute(
                    text("""
                        INSERT INTO location (
                            name,
                            administrative_level,
                            latitude,
                            longitude,
                            created_at
                        )
                        VALUES (
                            'FloodWatch E2E Test Point',
                            'test_observation_point',
                            -1.2921,
                            36.8219,
                            NOW()
                        )
                        RETURNING location_id
                    """)
                ).scalar_one()

                check(test_location_id is not None, "Temporary monitoring location created")

                test_engine = TransactionBoundEngine(connection)

                # FIRST EXECUTION — 06:00 EAT
                print("\n--- MORNING RUN: 06:00 EAT ---")
                morning_prediction = make_prediction(test_location_id, assessment_date, 0.40)
                morning_ids = persist_predictions(test_engine, morning_prediction, model_id, morning)
                check(len(morning_ids) == 1, "Morning run returned one prediction ID")

                prediction_id = morning_ids[0]
                first_predictions = prediction_records(connection, test_location_id)
                check(len(first_predictions) == 1, "Morning run inserted one prediction")

                first_alerts_summary = persist_alerts(test_engine, morning_prediction, morning_ids, issued_at=morning)
                check(first_alerts_summary["created"] == 1, "Morning run created one advisory")

                first_alerts = alert_records(connection, prediction_id)
                check(len(first_alerts) == 1 and first_alerts[0]["status"] == "ACTIVE", "Morning advisory is active")

                original_alert_id = (first_alerts[0]["alert_id"])

                #SECOND EXECUTION — 18:00 EAT
                print("\n--- EVENING RUN: 18:00 EAT ---")
                evening_prediction = make_prediction(test_location_id, assessment_date, 0.43)
                evening_ids = persist_predictions(test_engine, evening_prediction, model_id, evening)
                check(evening_ids == morning_ids, "Evening run reused the existing prediction ID")

                second_predictions = prediction_records(connection, test_location_id)
                check(len(second_predictions) == 1, "Same-day rerun created no duplicate prediction")
                check(abs(float(second_predictions[0]["risk_score"]) - 0.43) < 1e-9, "Existing prediction score was updated")
                check(second_predictions[0]["prediction_time"] == evening, "Prediction timestamp was refreshed")

                second_alerts_summary = persist_alerts(test_engine, evening_prediction, evening_ids, issued_at=evening)
                check(second_alerts_summary["created"] == 0 and second_alerts_summary["updated"] == 1, "Evening run updated the existing advisory")

                second_alerts = alert_records(connection, prediction_id)
                check(len(second_alerts) == 1, "Same-day rerun created no duplicate advisory")
                check(second_alerts[0]["alert_id"] == original_alert_id, "Original alert ID was preserved")
                check(second_alerts[0]["issued_at"] == evening, "Advisory issue timestamp was refreshed")

            finally:
                # This SAVEPOINT is explicitly rolled back, whether the assertions passed or failed.
                transaction.rollback()
                print("\n[INFO] Test transaction rolled back")

            if test_location_id is not None:
                remaining = connection.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM location
                        WHERE location_id = :location_id
                    """),
                    {"location_id": test_location_id},
                ).scalar_one()

                check(remaining == 0, "Temporary monitoring location was removed")
            check(database_counts(connection) == baseline, "Database record counts match the baseline")
    finally:
        engine.dispose()

    print("\n" + "=" * 50)
    print("E2E-03B/C RESULT: PASS")
    print("=" * 50)
    print("Two sequential operational persistence executions completed successfully.")
    print("All temporary changes were rolled back.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-03B/C RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        raise
