import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text

from src.operational.persistence import (create_database_engine, load_operational_locations)

NAIROBI_TZ = ZoneInfo("Africa/Nairobi")
MODEL_NAME = "nairobi_floodwatch_random_forest"

def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print(f"[PASS] {description}", flush=True)

def read_snapshot(engine, assessment_date, model_id):
    #Read existing operational records without modifying the database.
    with engine.connect() as connection:
        predictions = connection.execute(
            text("""
                SELECT
                    location_id,
                    prediction_id,
                    prediction_time,
                    risk_level,
                    risk_score

                FROM flood_risk_prediction

                WHERE model_id = :model_id
                  AND (
                      prediction_time
                      AT TIME ZONE 'Africa/Nairobi'
                  )::date = :assessment_date

                ORDER BY location_id
            """),
            {
                "model_id": model_id,
                "assessment_date": assessment_date,
            },
        ).mappings().all()

        alerts = connection.execute(
            text("""
                SELECT
                    a.alert_id,
                    a.prediction_id,
                    a.status

                FROM flood_alert a

                JOIN flood_risk_prediction p
                    ON p.prediction_id = a.prediction_id

                WHERE p.model_id = :model_id
                  AND (
                      p.prediction_time
                      AT TIME ZONE 'Africa/Nairobi'
                  )::date = :assessment_date

                ORDER BY a.prediction_id
            """),
            {
                "model_id": model_id,
                "assessment_date": assessment_date,
            },
        ).mappings().all()

        totals = connection.execute(
            text("""
                SELECT
                    (SELECT COUNT(*)
                     FROM flood_risk_prediction)
                        AS predictions,

                    (SELECT COUNT(*)
                     FROM flood_alert)
                        AS alerts
            """)
        ).mappings().one()

    prediction_map = {int(row["location_id"]): dict(row) for row in predictions}
    alert_map = {int(row["prediction_id"]): dict(row) for row in alerts}

    check(len(prediction_map) == len(predictions), "No duplicate location predictions in snapshot")
    check(len(alert_map) == len(alerts), "No duplicate alerts per prediction in snapshot")

    return {
        "predictions": prediction_map,
        "alerts": alert_map,
        "totals": dict(totals),
    }

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-03E")
    print("=" * 50)

    assessment_date = datetime.now(NAIROBI_TZ).date()

    print("Assessment date:", assessment_date)
    print("Mode: FULL OPERATIONAL DATABASE RERUN")

    engine = create_database_engine()

    try:
        locations = load_operational_locations(engine)
        expected_ids = set(locations["location_id"].astype(int))
        expected_count = len(expected_ids)

        with engine.connect() as connection:
            model_id = connection.execute(
                text("""
                    SELECT model_id
                    FROM model

                    WHERE name = :name
                      AND is_active = TRUE

                    ORDER BY model_id DESC
                    LIMIT 1
                """),
                {"name": MODEL_NAME},
            ).scalar_one_or_none()

        check(model_id is not None, "Active production model is registered")

        #DATABASE SNAPSHOT BEFORE RERUN
        print("\n--- BEFORE OPERATIONAL RUN ---")

        before = read_snapshot(engine, assessment_date, model_id)
        check(set(before["predictions"]) == expected_ids, "Existing daily predictions cover all locations")

        print("Predictions:", before["totals"]["predictions"])
        print("Alerts:", before["totals"]["alerts"])
        print("Current-day prediction records:", len(before["predictions"]))
        print("Current-day alert records:", len(before["alerts"]))

        #ACTUAL OPERATIONAL EXECUTION
        print("\n--- RUNNING OPERATIONAL PIPELINE ---")
        sys.stdout.flush()

        result = subprocess.run(
            [sys.executable, "-m", "scripts.run_operational_prediction"],
            check=False,
        )

        check(result.returncode == 0, "Operational pipeline completed successfully")

        # Prevent comparisons across different dates.
        check(datetime.now(NAIROBI_TZ).date() == assessment_date, "Both executions use the same Nairobi date")

        #DATABASE SNAPSHOT AFTER RERUN
        print("\n--- AFTER OPERATIONAL RUN ---")

        after = read_snapshot(engine, assessment_date, model_id)
        check(set(after["predictions"]) == expected_ids, "All locations remain covered after rerun")

        #PREDICTION IDENTITY
        check(before["totals"]["predictions"] == after["totals"]["predictions"], "Total prediction count remained unchanged")

        preserved_ids = all(before["predictions"][location_id]["prediction_id"] == after["predictions"][location_id]["prediction_id"] for location_id in expected_ids)

        check(preserved_ids, "All existing prediction IDs were preserved")

        # PREDICTION TIMESTAMPS
        previous_times = [
            before["predictions"][location_id]["prediction_time"]
            for location_id in expected_ids]

        updated_times = [
            after["predictions"][location_id]["prediction_time"]
            for location_id in expected_ids]

        check(all(new >= previous for previous, new in zip(previous_times, updated_times)), "Prediction timestamps did not move backward")
        check(any(new > previous for previous, new in zip(previous_times, updated_times)), "Prediction timestamps were refreshed")

        #PREDICTION VALIDITY
        valid_predictions = all(row["risk_level"] in {"LOW", "MODERATE", "HIGH"} and row["risk_score"] is not None and 0 <= float(row["risk_score"]) <= 1 for row in after["predictions"].values())
        check(valid_predictions, "All updated predictions contain valid risk values")

        #ALERT IDENTITY
        check(before["totals"]["alerts"] == after["totals"]["alerts"], "No additional alert rows were created")

        preserved_alert_ids = all(
            prediction_id in after["alerts"] and after["alerts"][prediction_id]["alert_id"] == previous["alert_id"]
            for prediction_id, previous in before["alerts"].items()
        )

        check(preserved_alert_ids, "Existing alert IDs were preserved")

        #SUMMARY
        print("\n" + "=" * 50)
        print("E2E-03E RESULT: PASS")
        print("=" * 50)
        print(f"Monitoring locations: {expected_count}")
        print("Predictions before:", before["totals"]["predictions"])
        print("Predictions after:", after["totals"]["predictions"])
        print("Alerts before:", before["totals"]["alerts"])
        print("Alerts after:", after["totals"]["alerts"])
        print("Sequential same-day rerun verified.")
    finally:
        engine.dispose()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-03E RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1)
