import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from src.dashboard.database import fetch_rows
from src.operational.persistence import (create_database_engine, load_operational_locations)

NAIROBI_TZ = ZoneInfo("Africa/Nairobi")

def main():
    failures = []

    def check(condition, description):
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {description}")

        if not condition:
            failures.append(description)

    def query_one(sql):
        rows = fetch_rows(sql, {})
        if not rows:
            raise AssertionError("Expected a database result but received none.")

        return rows[0]

    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-03A")
    print("=" * 50)
    print("Mode: READ-ONLY DATABASE VALIDATION")
    print("Local date:", datetime.now(NAIROBI_TZ).date())

    #OPERATIONAL MONITORING LOCATIONS
    print("\n--- MONITORING LOCATIONS ---")

    engine = create_database_engine()
    locations = load_operational_locations(engine)
    expected_locations = len(locations)

    print("Operational locations:", expected_locations)
    check(expected_locations > 0, "Operational monitoring locations available")
    check(locations["location_id"].is_unique, "Operational location identifiers are unique")

    #HISTORICAL PREDICTION INTEGRITY
    print("\n--- PREDICTION INTEGRITY ---")

    integrity = query_one("""
        SELECT
            COUNT(*) AS total_predictions,

            COUNT(*) FILTER (
                WHERE risk_score IS NULL
                   OR risk_score < 0
                   OR risk_score > 1
            ) AS invalid_scores,

            COUNT(*) FILTER (
                WHERE risk_level IS NULL
                   OR risk_level NOT IN (
                       'LOW', 'MODERATE', 'HIGH'
                   )
            ) AS invalid_risk_levels

        FROM flood_risk_prediction
    """)

    print("Stored predictions:", integrity["total_predictions"])
    print("Invalid scores:", integrity["invalid_scores"])
    print("Invalid classifications:", integrity["invalid_risk_levels"])

    check(integrity["total_predictions"] > 0, "Historical predictions are available")
    check(integrity["invalid_scores"] == 0, "All stored model scores are valid")
    check(integrity["invalid_risk_levels"] == 0, "All stored risk classifications are valid")

    #SAME-DAY PREDICTION DUPLICATES
    print("\n--- SAME-DAY PREDICTION UNIQUENESS ---")
    duplicate_summary = query_one("""
        WITH daily_predictions AS (
            SELECT
                location_id,
                model_id,

                (
                    prediction_time
                    AT TIME ZONE 'Africa/Nairobi'
                )::date AS local_date,

                COUNT(*) AS copies

            FROM flood_risk_prediction

            GROUP BY
                location_id,
                model_id,
                (
                    prediction_time
                    AT TIME ZONE 'Africa/Nairobi'
                )::date

            HAVING COUNT(*) > 1
        )

        SELECT
            COUNT(*) AS duplicate_groups,

            COALESCE(
                SUM(copies - 1),
                0
            ) AS excess_rows

        FROM daily_predictions
    """)

    print("Duplicate location/model/day groups:", duplicate_summary["duplicate_groups"])
    print("Excess prediction rows:", duplicate_summary["excess_rows"])

    check(duplicate_summary["duplicate_groups"] == 0, "No duplicate predictions for the same location, model and Nairobi calendar date")

    #LATEST ASSESSMENT COVERAGE
    print("\n--- LATEST ASSESSMENT COVERAGE ---")
    latest_rows = fetch_rows("""
        WITH latest_day AS (
            SELECT MAX(
                (
                    prediction_time
                    AT TIME ZONE 'Africa/Nairobi'
                )::date
            ) AS assessment_date

            FROM flood_risk_prediction
        )

        SELECT
            d.assessment_date,

            COUNT(*) AS predictions,

            COUNT(DISTINCT p.location_id)
                AS monitored_locations,

            COUNT(*) FILTER (
                WHERE
                    p.prediction_data::jsonb
                        -> 'predictors'
                        ->> 'rain_1d' IS NOT NULL

                AND p.prediction_data::jsonb
                        -> 'predictors'
                        ->> 'rain_3d' IS NOT NULL

                AND p.prediction_data::jsonb
                        -> 'predictors'
                        ->> 'rain_7d' IS NOT NULL
            ) AS predictions_with_rainfall

        FROM latest_day d

        JOIN flood_risk_prediction p
            ON (
                p.prediction_time
                AT TIME ZONE 'Africa/Nairobi'
            )::date = d.assessment_date

        GROUP BY d.assessment_date
    """, {})

    if latest_rows:
        latest = latest_rows[0]
        print("Latest assessment date:", latest["assessment_date"])
        print("Predictions on latest date:", latest["predictions"])
        print("Locations on latest date:", latest["monitored_locations"])
        print("Predictions with rainfall:", latest["predictions_with_rainfall"])

        check(latest["monitored_locations"] == expected_locations, "Latest assessment covers all operational locations")
        check(latest["predictions_with_rainfall"] == latest["predictions"], "Latest predictions retain all three rainfall accumulation features")

    else:
        check(False, "Latest assessment records available")

    #ALERT TABLE BASELINE
    print("\n--- E. ALERT LIFECYCLE BASELINE ---")

    alert_summary = query_one("""
        SELECT COUNT(*) AS total_alerts
        FROM flood_alert
    """)

    print("Total stored alerts:", alert_summary["total_alerts"])

    columns = fetch_rows("""
        SELECT
            column_name,
            data_type

        FROM information_schema.columns

        WHERE table_schema = current_schema()
          AND table_name = 'flood_alert'

        ORDER BY ordinal_position
    """, {})

    column_names = {row["column_name"] for row in columns}
    print("Alert table columns:", ", ".join(sorted(column_names)))
    check(len(column_names) > 0, "Alert table schema is available")

    if "is_active" in column_names:
        active = query_one("""
            SELECT
                COUNT(*) AS active_alerts
            FROM flood_alert
            WHERE is_active = TRUE
        """)

        print("Currently active alerts:", active["active_alerts"])

    #We will inspect the actual alert schema and implementation before testing write operations.
    #FINAL RESULT
    print("\n" + "=" * 50)

    if failures:
        print("E2E-03A RESULT: FAIL")

        for failure in failures:
            print(" -", failure)

        return 1

    print("E2E-03A RESULT: PASS")
    print("=" * 50)

    print("Read-only baseline completed successfully.")
    print("No database records were modified.")

    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("\nE2E-03A RESULT: FAIL\n" f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
