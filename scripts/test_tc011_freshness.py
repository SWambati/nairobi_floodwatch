#TC-011: FloodWatch Operational Prediction Freshness Audit
#Purpose: Verify monitoring-location coverage, active model predictions, data integrity, model feature completeness, and prediction freshness.
#Mode: READ-ONLY. No operational database records are modified.

import json
import math
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.dashboard.database import fetch_rows
from src.model.model_dataset import PREDICTOR_COLUMNS

# Configuration
EXPECTED_LOCATIONS = 237
NAIROBI_TIMEZONE = ZoneInfo("Africa/Nairobi")

failures = []

# Timestamp helper
def to_utc(value):
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if timestamp.tzinfo is None:
        raise ValueError("Database timestamp has no timezone information.")

    return timestamp.astimezone(timezone.utc)

# Test result helper
def check(description, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}" + (f" | {detail}" if detail else ""), flush=True)

    if not condition:
        failures.append(description)

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH - TC-011")
    print("OPERATIONAL DATA FRESHNESS AND INTEGRITY AUDIT")
    print("=" * 50)
    print("Mode: READ-ONLY")
    print()

    #Database environment
    metadata = fetch_rows(
        """
        SELECT
            current_database() AS db_name,
            CURRENT_TIMESTAMP AS db_now
        """,
        {},
    )[0]

    now = to_utc(metadata["db_now"])

    print("--- DATABASE ENVIRONMENT ---")
    print("Database:", metadata["db_name"])
    print("Database time (EAT):", now.astimezone(NAIROBI_TIMEZONE).isoformat())
    print()

    #Configured monitoring locations
    print("--- MONITORING LOCATION COVERAGE ---")
    locations = fetch_rows(
        """
        SELECT location_id
        FROM location
        """,
        {},
    )

    location_ids = {int(row["location_id"]) for row in locations}
    check("Expected 237 monitoring locations", len(location_ids) == EXPECTED_LOCATIONS, f"Found: {len(location_ids)}")

    #Active model
    print("\n--- ACTIVE MODEL ---")
    active_models = fetch_rows(
        """
        SELECT model_id, name, version
        FROM model
        WHERE is_active = TRUE
        """,
        {},
    )

    check("Exactly one active prediction model", len(active_models) == 1, f"Active models: {len(active_models)}")

    for model in active_models:
        print("Active model:", model["name"], "| Version:", model["version"])

    #Latest operational predictions
    print("\n--- PREDICTION COVERAGE ---")
    latest_predictions = fetch_rows(
        """
        SELECT DISTINCT ON (p.location_id)
            p.location_id,
            p.prediction_id,
            p.prediction_time,
            p.created_at,
            p.risk_score,
            p.risk_level,
            p.prediction_data

        FROM flood_risk_prediction AS p

        JOIN model AS m
            ON m.model_id = p.model_id

        WHERE m.is_active = TRUE

        ORDER BY
            p.location_id,
            p.prediction_time DESC,
            p.prediction_id DESC
        """,
        {},
    )

    predicted_ids = {int(row["location_id"]) for row in latest_predictions}
    missing_locations = location_ids - predicted_ids
    unexpected_locations = predicted_ids - location_ids

    check(
        "Every monitoring location has a prediction",
        (
            predicted_ids == location_ids
            and len(latest_predictions) == EXPECTED_LOCATIONS
        ),
        (
            f"Predictions: {len(latest_predictions)} | "
            f"Missing: {len(missing_locations)} | "
            f"Unexpected: {len(unexpected_locations)}"
        )
    )

    #Risk score and classification validity
    print("\n--- PREDICTION VALIDITY ---")
    invalid_scores = []
    invalid_features = []
    timestamps = []
    assessment_dates = []

    for row in latest_predictions:
        location_id = row["location_id"]

        # Validate risk score and category
        try:
            score = float(row["risk_score"])
            valid_score = (math.isfinite(score) and 0.0 <= score <= 1.0)
            valid_level = (str(row["risk_level"]).upper() in {"LOW", "MODERATE", "HIGH"})

            if not (valid_score and valid_level):
                invalid_scores.append(location_id)

        except (TypeError, ValueError, OverflowError):
            invalid_scores.append(location_id)

        # Validate model predictors
        prediction_data = row["prediction_data"]

        if isinstance(prediction_data, str):
            try:
                prediction_data = json.loads(prediction_data)
            except json.JSONDecodeError:
                prediction_data = None

        predictors = (prediction_data.get("predictors") if isinstance(prediction_data, dict) else None)

        try:
            valid_features = (isinstance(predictors, dict) and all(feature in predictors and math.isfinite(float(predictors[feature])) for feature in PREDICTOR_COLUMNS))

            if not valid_features:
                invalid_features.append(location_id)

        except (TypeError, ValueError, OverflowError):
            invalid_features.append(location_id)

        # Collect timestamps for freshness analysis
        timestamp = to_utc(row["prediction_time"])
        timestamps.append(timestamp)
        assessment_dates.append(timestamp.astimezone(NAIROBI_TIMEZONE).date())

    check("All risk scores and classifications are valid", len(invalid_scores) == 0, f"Invalid locations: {len(invalid_scores)}")
    check(
        "All predictions contain the required 15 features",
        (
            len(PREDICTOR_COLUMNS) == 15
            and len(invalid_features) == 0
        ),
        (
            f"Expected features: 15 | "
            f"Schema features: {len(PREDICTOR_COLUMNS)} | "
            f"Invalid locations: {len(invalid_features)}"
        )
    )

    #Duplicate prediction records
    print("\n--- DUPLICATE DETECTION ---")
    duplicate_groups = fetch_rows(
        """
        SELECT
            p.location_id,
            p.model_id,
            (
                p.prediction_time
                AT TIME ZONE 'Africa/Nairobi'
            )::date AS local_date,
            COUNT(*) AS record_count

        FROM flood_risk_prediction AS p

        JOIN model AS m
            ON m.model_id = p.model_id

        WHERE m.is_active = TRUE

        GROUP BY
            p.location_id,
            p.model_id,
            (
                p.prediction_time
                AT TIME ZONE 'Africa/Nairobi'
            )::date

        HAVING COUNT(*) > 1

        LIMIT 10
        """,
        {},
    )

    check("No duplicate location/model/day predictions", len(duplicate_groups) == 0, f"Duplicate groups found (up to 10): {len(duplicate_groups)}")

    #Prediction date and freshness
    print("\n--- DATA FRESHNESS ---")
    if timestamps:
        date_counts = Counter(assessment_dates)
        latest_date = max(date_counts)

        check(
            "All latest predictions share the latest assessment date",
            (
                len(date_counts) == 1
                and date_counts[latest_date]
                == EXPECTED_LOCATIONS
            ),
            (
                f"Latest date: {latest_date} | "
                f"Locations: {date_counts[latest_date]}/"
                f"{EXPECTED_LOCATIONS}"
            ),
        )

        newest = max(timestamps)
        oldest = min(timestamps)
        youngest_age_hours = (now - newest).total_seconds() / 3600
        oldest_age_hours = (now - oldest).total_seconds() / 3600

        print("Latest assessment (EAT):", newest.astimezone(NAIROBI_TIMEZONE).isoformat())
        print("Oldest latest assessment (EAT):", oldest.astimezone(NAIROBI_TIMEZONE).isoformat())
        print(f"Youngest assessment age: {youngest_age_hours:.2f} hours")
        print(f"Oldest assessment age: {oldest_age_hours:.2f} hours")
        check("No future-dated predictions", youngest_age_hours >= -0.02)
        print("\nNOTE: Freshness ages are measured, not automatically judged against an unapproved acceptance threshold.")
        print("NOTE: prediction_time is the assessment timestamp, while created_at may reflect the original insertion rather than a rerun.")

    else:
        check("Latest assessment records exist", False)

    #Final summary
    print("\n" + "=" * 50)

    if failures:
        print("TC-011 DATABASE AUDIT: FAIL")
        print(f"Failed checks: {len(failures)}")

        for failure in failures:
            print(" -", failure)

        raise SystemExit(1)

    print("TC-011 DATABASE AUDIT: PASS")
    print("All checked database integrity conditions satisfied.")

    print("=" * 50)

if __name__ == "__main__":
    main()
