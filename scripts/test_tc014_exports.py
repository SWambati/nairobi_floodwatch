"""
TC-014: FloodWatch CSV Export Tests
Coverage:
- CSV headers, encoding and filename
- Nairobi-local date ordering
- Data merging and missing values
- CSV formula-injection protection
- HTTP response security headers
- Date-range validation
- Empty and missing history
- Production anonymous-download protection
- Production public-history availability
PDF checks will follow after locating the browser implementation.
this is a  READ-ONLY test
"""
import codecs
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import requests
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.auth.authorization import require_verified_resident
from src.dashboard.routes.resident import (router, _safe_csv_cell)

BASE_URL = os.getenv("FLOODWATCH_BASE_URL", "https://nairobi-floodwatch.onrender.com",).rstrip("/")
OUTPUT = Path("outputs/testing/tc014_csv_evidence.json")
EXPECTED_HEADER = ["date", "model_score", "risk_level", "precipitation_snapshot_mm", "temperature_snapshot_c"]
RESULTS = []

#Isolated test application
test_app = FastAPI()
test_app.include_router(router)

#Only the isolated local TestClient overrides the dependency. The real Render app is NOT modified.
test_app.dependency_overrides[require_verified_resident] = lambda: {"role": "resident", "email_verified": True}

#Controlled test fixture
SAMPLE_HISTORY = {
    "predictions": [
        {
            "local_date": "2026-11-05",
            "risk_score": 0.38,
            "risk_level": "MODERATE"
        },
        {
            "local_date": "2026-11-07",
            "risk_score": 0.82,
            "risk_level": "HIGH"
        }
    ],
    "weather_readings": [
        {
            "local_date": "2026-11-06",
            "rainfall": 1.2,
            "temperature": 23.0
        },
        {
            "local_date": "2026-11-07",
            "rainfall": 7.9,
            "temperature": 21.5
        }
    ]
}

#Helpers
def check(condition, message):
    if not condition:
        raise AssertionError(message)

def execute(name, function):
    try:
        detail = function()
        RESULTS.append({"test": name, "status": "PASS", "detail": str(detail or "")})
        print(f"[PASS] {name}" + (f" | {detail}" if detail else ""), flush=True)

    except Exception as exc:
        RESULTS.append({"test": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"})
        print(f"[FAIL] {name} | " f"{type(exc).__name__}: {exc}", flush=True)

def get_fixture_csv(client, history=SAMPLE_HISTORY):
    with patch("src.dashboard.routes.resident._load_location_history", return_value=history):
        return client.get("/api/resident/locations/1/history.csv")

def parse_csv(response):
    decoded = response.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))
    return reader.fieldnames, list(reader)

#Test cases
def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH - TC-014")
    print("CSV EXPORT TESTING")
    print("=" * 50)
    print("Mode: READ-ONLY")
    print("Production target:", BASE_URL)
    print()

    with TestClient(test_app) as client:
        response = get_fixture_csv(client)

        #Successful CSV generation
        def test_csv_delivery():
            check(response.status_code == 200, f"Expected 200, got {response.status_code}")
            check(response.headers["content-type"].startswith("text/csv"), "Incorrect CSV content type")
            check(len(response.content) > 0, "CSV is empty")

            return "HTTP 200, text/csv"

        execute("CSV export returns a valid download", test_csv_delivery)

        #Filename and security headers
        def test_headers():
            disposition = response.headers.get("content-disposition", "")
            check('filename="floodwatch_location_1_history.csv"' in disposition, "Incorrect filename")
            check(response.headers.get("cache-control") == "no-store", "Missing no-store header")
            check(response.headers.get("x-content-type-options") == "nosniff", "Missing nosniff header")

            return "Filename and security headers valid"

        execute("CSV filename and download headers", test_headers)

        #Encoding and structure
        def test_csv_structure():
            check(response.content.startswith(codecs.BOM_UTF8), "Missing UTF-8 BOM")
            header, rows = parse_csv(response)
            check(header == EXPECTED_HEADER, f"Unexpected header: {header}")
            check(len(rows) == 3, f"Expected 3 merged dates, got {len(rows)}")
            check(response.content.count(b"\r\n") == 4, "Expected CRLF line endings")

            return "UTF-8 BOM, 5 columns, 3 data rows"

        execute("CSV encoding and structure", test_csv_structure)

        #Nairobi-local date ordering
        def test_date_order():
            _, rows = parse_csv(response)
            actual = [row["date"] for row in rows]
            expected = ["2026-11-05", "2026-11-06", "2026-11-07"]
            check(actual == expected, f"Unexpected dates: {actual}")
            return "Dates sorted chronologically"

        execute("CSV records ordered by date", test_date_order)

        #Prediction and weather merging
        def test_merged_values():
            _, rows = parse_csv(response)
            by_date = {row["date"]: row for row in rows}
            november_7 = by_date["2026-11-07"]

            check(november_7["model_score"] == "0.82", "Model score does not match fixture")
            check(november_7["risk_level"] == "HIGH", "Risk category does not match fixture")
            check(november_7["precipitation_snapshot_mm"] == "7.9", "Rainfall does not match fixture")
            check(november_7["temperature_snapshot_c"]  == "21.5", "Temperature does not match fixture")

            return "Prediction and weather values match"

        execute("CSV values correctly combined", test_merged_values)

        #Missing data remains blank
        def test_missing_data():
            _, rows = parse_csv(response)
            by_date = {row["date"]: row for row in rows}
            weather_only = by_date["2026-11-06"]

            check(weather_only["model_score"] == "", "Missing model score was fabricated")
            check(weather_only["risk_level"] == "", "Missing risk level was fabricated")
            check(weather_only["precipitation_snapshot_mm"] == "1.2", "Recorded weather value missing")

            return "Missing assessments remain blank"

        execute("CSV does not invent missing values", test_missing_data)

        #Spreadsheet formula-injection safety
        def test_formula_protection():
            dangerous = ("=1+1", "+SUM(1,2)", "-CMD()", "@SUM(1,2)", "   =HYPERLINK()")

            for value in dangerous:
                protected = _safe_csv_cell(value)
                check(protected.startswith("'"), f"Unsafe value: {value}")

            check(_safe_csv_cell(0.42) == 0.42, "Numeric score was changed")
            malicious_history = {
                "predictions": [{
                    "local_date": "2026-11-07",
                    "risk_score": 0.42,
                    "risk_level": "=HYPERLINK()"
                }],
                "weather_readings": []
            }

            malicious_response = get_fixture_csv(client, malicious_history)
            check(malicious_response.status_code == 200, "Malicious fixture export failed")
            _, rows = parse_csv(malicious_response)
            check(rows[0]["risk_level"].startswith("'="), "CSV output did not neutralize formula")

            return "Formula-like text neutralized"

        execute("CSV spreadsheet-injection protection", test_formula_protection)

        #Date-range boundaries
        def test_date_boundaries():
            with patch("src.dashboard.routes.resident._load_location_history", return_value=SAMPLE_HISTORY):
                for days in (1, 30, 90):
                    result = client.get("/api/resident/locations/1/history.csv", params={"days": days})
                    check(result.status_code == 200, f"days={days}: {result.status_code}")

                for days in (0, 91):
                    result = client.get("/api/resident/locations/1/history.csv", params={"days": days})
                    check(result.status_code == 422, f"days={days}: {result.status_code}")

            return "1, 30, 90 accepted; 0, 91 rejected"

        execute("CSV date-range validation", test_date_boundaries)

        #Empty monitoring history
        def test_empty_history():
            result = get_fixture_csv(client, {"predictions": [], "weather_readings": []})
            check(result.status_code == 404, f"Expected 404, got {result.status_code}")
            return "HTTP 404 for empty history"

        execute("CSV unavailable when no history exists", test_empty_history)

        #Invalid location
        def test_unknown_location():
            with patch("src.dashboard.routes.resident._load_location_history", side_effect=HTTPException(status_code=404, detail="Monitoring point not found.")):
                result = client.get("/api/resident/locations/999999/history.csv")

            check(result.status_code == 404, f"Expected 404, got {result.status_code}")
            return "HTTP 404 for unknown location"

        execute("CSV rejects unknown monitoring locations", test_unknown_location)

    #Production anonymous access restriction
    print("\n--- PRODUCTION SECURITY ---")
    def test_production_access():
        result = requests.get(BASE_URL + "/api/resident/locations/1/history.csv", timeout=(15, 90), allow_redirects=False)
        allowed_denials = {301, 302, 303, 307, 308, 401, 403}
        check(result.status_code in allowed_denials, (f"Anonymous CSV download was not denied as expected: HTTP {result.status_code}"))
        return f"Anonymous request: HTTP {result.status_code}"

    execute("Production CSV requires authentication", test_production_access)

    #Public history remains accessible
    def test_public_history():
        result = requests.get(BASE_URL + "/api/resident/locations/1/history", params={"days": 30}, timeout=(15, 90))
        check(result.status_code == 200, f"Public history returned {result.status_code}")

        payload = result.json()
        check(isinstance(payload, dict), "Expected history object")
        check(isinstance(payload.get("predictions"), list), "Expected predictions list")

        return (f"{len(payload['predictions'])} prediction history records")

    execute("Production public history remains accessible", test_public_history)

    #Evidence
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "test_id": "TC-014",
                "scope": "CSV export",
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "production_target": BASE_URL,
                "results": RESULTS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    passed = sum(item["status"] == "PASS" for item in RESULTS)
    failed = sum(item["status"] == "FAIL" for item in RESULTS)

    print("\n" + "=" * 50)
    print("TC-014 CSV RESULTS")
    print("Passed:", passed)
    print("Failed:", failed)
    print("Evidence:", OUTPUT)

    if failed:
        print("RESULT: FAIL")
        sys.exit(1)

    print("RESULT: PASS")
    print("NOTE: Authenticated production CSV downloads and PDF exports require separate testing.")
    print("=" * 50)

if __name__ == "__main__":
    main()
