import math
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

from src.dashboard.database import fetch_rows

BASE_URL = "http://127.0.0.1:8010"
NAIROBI_TZ = ZoneInfo("Africa/Nairobi")

RAINFALL_FEATURES = ("rain_1d", "rain_3d", "rain_7d")

def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print(f"[PASS] {description}", flush=True)

def get_api(path):
    response = requests.get(BASE_URL + path, timeout=15, allow_redirects=False, headers={"Accept": "application/json"})
    check(response.status_code == 200, f"{path} returned HTTP 200")
    return response.json()

def nairobi_date(timestamp):
    if isinstance(timestamp, datetime):
        value = timestamp
    else:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise AssertionError("Assessment timestamp has no timezone.")
    return value.astimezone(NAIROBI_TZ).date()

def close_enough(a, b, tolerance=0.005):
    if a is None or b is None:
        return a is None and b is None

    return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tolerance)

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-03F")
    print("=" * 50)
    print("Mode: READ-ONLY DATABASE/API CONSISTENCY")

    #Latest PostgreSQL predictions
    database_rows = fetch_rows("""
        SELECT DISTINCT ON (p.location_id)
            p.location_id,
            p.prediction_time,
            p.risk_score,
            p.risk_level,
            p.prediction_data

        FROM flood_risk_prediction p

        JOIN model m
            ON m.model_id = p.model_id

        WHERE m.is_active = TRUE

        ORDER BY
            p.location_id,
            p.prediction_time DESC,
            p.prediction_id DESC
    """, {})

    database = {str(row["location_id"]): row for row in database_rows}
    check(len(database) > 0, "Active-model predictions exist in PostgreSQL")
    print("Database monitoring locations:", len(database))

    #Latest monitoring API records
    print("\n--- MONITORING API ---")
    api_locations = get_api("/api/locations")
    check(isinstance(api_locations, list), "Monitoring API returned a location list")
    api = {str(point["location_id"]): point for point in api_locations}
    check(len(api) == len(api_locations), "API contains no duplicate monitoring locations")
    check(set(api) == set(database), "API locations match active PostgreSQL predictions")

    #Compare every risk assessment
    print("\n--- RISK ASSESSMENT CONSISTENCY ---")
    for location_id, point in api.items():
        stored = database[location_id]

        check(point["risk_level"] == stored["risk_level"], f"Location {location_id}: risk classification matches")
        check(close_enough(point["risk_score"], stored["risk_score"]), f"Location {location_id}: model score matches")
        check(nairobi_date(point["prediction_time"]) == nairobi_date(stored["prediction_time"]), f"Location {location_id}: assessment date matches")

    #Historical rainfall validation
    print("\n--- HISTORICAL RAINFALL CONSISTENCY ---")
    sorted_ids = sorted(api, key=int)
    sample_ids = {
        sorted_ids[0],
        sorted_ids[len(sorted_ids) // 2],
        sorted_ids[-1],
    }

    # Include Nairobi West when present.
    for location_id, point in api.items():
        if "nairobi west" in str(point.get("name", "")).lower():
            sample_ids.add(location_id)
            break

    for location_id in sorted(sample_ids, key=int):
        point_name = api[location_id].get("name", f"Point {location_id}")
        print(f"\nChecking: {point_name} " f"(ID {location_id})")

        history = get_api(f"/api/resident/locations/" f"{location_id}/history?days=30")
        predictions = history.get("predictions", [])
        check(isinstance(predictions, list) and len(predictions) > 0, f"Location {location_id}: historical records available")

        latest_history = max(predictions, key=lambda row: str(row["local_date"]))
        stored = database[location_id]
        prediction_data = stored["prediction_data"]

        if isinstance(prediction_data, str):
            import json
            prediction_data = json.loads(prediction_data)

        predictors = prediction_data.get("predictors", {})
        check(str(latest_history["local_date"]) == nairobi_date(stored["prediction_time"]).isoformat(), f"Location {location_id}: latest history date matches")
        check(close_enough(latest_history["risk_score"], stored["risk_score"]), f"Location {location_id}: historical score matches")

        for feature in RAINFALL_FEATURES:
            check(feature in predictors, f"Location {location_id}: " f"{feature} exists in PostgreSQL")
            check(close_enough(latest_history.get(feature), predictors[feature], tolerance=0.05), f"Location {location_id}: " f"{feature} matches PostgreSQL")

        print("Recorded rainfall (mm):", {key: latest_history.get(key) for key in RAINFALL_FEATURES})

    #Monitoring overview freshness
    print("\n--- OVERVIEW CONSISTENCY ---")
    overview = get_api("/api/overview")
    check("latest_prediction" in overview and overview["latest_prediction"] is not None, "Overview includes latest assessment timestamp")

    latest_database_date = max(nairobi_date(row["prediction_time"]) for row in database.values())
    check(nairobi_date(overview["latest_prediction"]) == latest_database_date, "Overview assessment date matches PostgreSQL")

    #Result
    print("\n" + "=" * 50)
    print("E2E-03F RESULT: PASS")
    print("=" * 50)
    print(f"Monitoring locations checked: {len(api)}")
    print(f"Rainfall history points checked: {len(sample_ids)}")
    print(f"Latest assessment date: {latest_database_date}")
    print("All checked API records match PostgreSQL.")
    print("No database records were modified.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-03F RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
