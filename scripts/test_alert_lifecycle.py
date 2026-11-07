#Validate flood-alert lifecycle behavior without changing persisted data.

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import pandas as pd
from sqlalchemy import text

from src.operational.alerts import persist_alerts
from src.operational.persistence import create_database_engine


class TransactionBoundEngine:
    # Keep persist_alerts() on the caller's rollback-only connection.

    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        # persist_alerts() expects engine.begin() function. Returning this connection prevents it from committing independently of the outer test.
        return nullcontext(self.connection)


def prediction_frame(location_id: int, risk_level: str, score: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "location_id": location_id,
        "location_name": "FloodWatch Lifecycle Test Point",
        "risk_level": risk_level,
        "flood_probability": score,
    }])


def insert_test_prediction(connection, location_id: int, model_id: int, prediction_time: datetime, risk_level: str, score: float) -> int:
    return connection.execute(
        text("""
            INSERT INTO flood_risk_prediction (location_id, model_id, prediction_time, risk_level, risk_score, created_at)
            VALUES (
                :location_id, :model_id, :prediction_time, :risk_level,
                :risk_score, NOW()
            )
            RETURNING prediction_id
        """),
        {
            "location_id": location_id,
            "model_id": model_id,
            "prediction_time": prediction_time,
            "risk_level": risk_level,
            "risk_score": score,
        },
    ).scalar_one()


def alerts_for_prediction(connection, prediction_id: int) -> list[dict]:
    rows = connection.execute(
        text("""
            SELECT alert_id, alert_level, status
            FROM flood_alert
            WHERE prediction_id = :prediction_id
            ORDER BY alert_id
        """),
        {"prediction_id": prediction_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def main():
    print("Nairobi FloodWatch Alert Lifecycle Tests")

    print("=" * 50)

    engine = create_database_engine()
    
    # TC-013 safety: lifecycle simulations must use local PostgreSQL.
    # The test creates and updates records within a rollback transaction.
    database_host = (engine.url.host or "").lower()
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}

    if database_host not in allowed_hosts:
        engine.dispose()
        raise RuntimeError(f"TC-013 lifecycle test must run against local PostgreSQL. Detected database host: {database_host}. Production Neon testing is not permitted.")

    print("Database environment: LOCAL PostgreSQL")
    print("Transaction policy: ROLLBACK ONLY")


    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            test_location_id = None

            try:
                # Create a test-only observation point in an uncommitted transaction.
                # Using a separate location avoids superseding production advisories.
                model_id = connection.execute(
                    text("""
                        SELECT model_id FROM model
                        WHERE name = 'nairobi_floodwatch_random_forest'
                          AND is_active = TRUE
                        ORDER BY model_id DESC LIMIT 1
                    """)
                ).scalar_one_or_none()
                if model_id is None:
                    raise RuntimeError("No active production RF found. Run the operational pipeline first.")

                test_location_id = connection.execute(
                    text("""
                        INSERT INTO location (name, administrative_level, latitude, longitude, created_at)
                        VALUES ('FloodWatch Lifecycle Test Point', 'test_observation_point', -1.2921, 36.8219, NOW())
                        RETURNING location_id
                    """)
                ).scalar_one()

                # Use controlled historical timestamps to simulate several days without waiting for real time to pass.
                # Start at 23:00 Nairobi time (20:00 UTC).
                #This allows the HIGH assessment two hours later to fall on the next Nairobi calendar day.
                t0 = (datetime.now(timezone.utc) - timedelta(days=10)).replace(hour=20, minute=0, second=0, microsecond=0)
                test_engine = TransactionBoundEngine(connection)

                # Test 1: First MODERATE assessment creates an ACTIVE ADVISORY.
                first_id = insert_test_prediction(connection, test_location_id, model_id, t0, "MODERATE", 0.40)
                result = persist_alerts(test_engine, prediction_frame(test_location_id, "MODERATE", 0.40), [first_id], issued_at=t0)
                assert result["created"] == 1
                assert len(alerts_for_prediction(connection, first_id)) == 1
                assert alerts_for_prediction(connection, first_id)[0]["status"] == "ACTIVE"
                assert alerts_for_prediction(connection, first_id)[0]["alert_level"] == "ADVISORY"
                print("PASS: MODERATE creates one active ADVISORY")

                # Test 2: Reprocessing the same prediction updates, never duplicates.
                result = persist_alerts(test_engine, prediction_frame(test_location_id, "MODERATE", 0.43), [first_id], issued_at=t0 + timedelta(hours=1))
                assert result["created"] == 0 and result["updated"] == 1
                assert len(alerts_for_prediction(connection, first_id)) == 1
                print("PASS: Same-prediction rerun updates without duplication")

                # Test 3: A newer HIGH assessment supersedes the old advisory.
                high_time = t0 + timedelta(hours=2)
                high_id = insert_test_prediction(connection, test_location_id, model_id, high_time, "HIGH", 0.82)
                result = persist_alerts(test_engine, prediction_frame(test_location_id, "HIGH", 0.82), [high_id], issued_at=high_time)
                assert result["superseded"] == 1 and result["created"] == 1
                assert alerts_for_prediction(connection, first_id)[0]["status"] == "SUPERSEDED"
                high_alert = alerts_for_prediction(connection, high_id)[0]
                assert high_alert["status"] == "ACTIVE" and high_alert["alert_level"] == "WARNING"
                print("PASS: New HIGH warning supersedes previous advisory")

                # Test 4: A LOW rerun resolves the existing HIGH warning.
                result = persist_alerts(test_engine, prediction_frame(test_location_id, "LOW", 0.04), [high_id], issued_at=t0 + timedelta(hours=3))
                assert result["resolved"] == 1
                assert alerts_for_prediction(connection, high_id)[0]["status"] == "RESOLVED"
                print("PASS: Falling to LOW resolves an existing warning")

                # Test 5: A 24-hour-old alert expires when a subsequent run occurs.
                third_time = t0 + timedelta(hours=28)
                third_id = insert_test_prediction(connection, test_location_id, model_id, third_time, "MODERATE", 0.44)
                persist_alerts(test_engine, prediction_frame(test_location_id, "MODERATE", 0.44), [third_id], issued_at=third_time)
                low_time = t0 + timedelta(hours=55)
                low_id = insert_test_prediction(connection, test_location_id, model_id, low_time, "LOW", 0.03)
                result = persist_alerts(test_engine, prediction_frame(test_location_id, "LOW", 0.03), [low_id], issued_at=low_time)
                assert result["expired"] >= 1
                assert alerts_for_prediction(connection, third_id)[0]["status"] == "EXPIRED"
                assert alerts_for_prediction(connection, low_id) == []
                active = connection.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM flood_alert AS a
                        JOIN flood_risk_prediction AS p ON p.prediction_id = a.prediction_id
                        WHERE p.location_id = :location_id AND a.status = 'ACTIVE'
                    """),
                    {"location_id": test_location_id},
                ).scalar_one()
                assert active == 0
                print("PASS: Expiration after 24 hours; LOW creates no active alert")

            finally:
                # This runs on success AND failure: neither test locations, predictions nor alerts are committed to the real database.
                transaction.rollback()

            if test_location_id is not None:
                remaining = connection.execute(
                    text("SELECT COUNT(*) FROM location WHERE location_id = :id"),
                    {"id": test_location_id},
                ).scalar_one()
                assert remaining == 0, "Test location unexpectedly persisted."
                print("PASS: All test database changes rolled back")

    finally:
        engine.dispose()

    print("\nAlert lifecycle tests: ALL PASSED")


if __name__ == "__main__":
    main()
