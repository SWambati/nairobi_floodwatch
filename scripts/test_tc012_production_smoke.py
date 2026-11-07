"""
#TC-012A: Nairobi FloodWatch Production Dashboard Smoke Tests
Tests:
- Production homepage and resident page delivery
- Resident HTML and accessibility structure
- Static CSS, JavaScript and branding assets
- Monitoring overview and 237 location records
- Prediction integrity and API consistency
- GIS boundary files
- Geographic search input validation
- Selected location rainfall-history responses

this is a read-only test. No database records are modified.
These are HTTP and HTML checks, NOT interactive browser tests.
"""
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import requests

BASE_URL = os.getenv("FLOODWATCH_BASE_URL", "https://nairobi-floodwatch.onrender.com").rstrip("/")
EXPECTED_LOCATIONS = 237
OUTPUT_PATH = Path("outputs/testing/tc012_production_smoke.json")
REQUIRED_RESIDENT_IDS = {
    "main",
    "freshness",
    "useLocation",
    "chooseArea",
    "clearLocation",
    "areaSearch",
    "riskFilter",
    "areaTitle",
    "areaRisk",
    "areaScore",
    "assessmentTime",
    "temperature",
    "humidity",
    "rainfall",
    "residentMap",
    "nearbyList",
    "advisoryStatus",
    "riskHistoryChart",
    "weatherHistoryChart",
    "downloadHistory",
    "geoSearchPanel",
    "geoSearchButton",
    "geoSearchLoading",
    "geoMatches",
    "geoContext",
}

RESULTS = []
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json, text/html, */*", "User-Agent": "FloodWatch-TC012-Testing"})

class HTMLInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.duplicates = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")

        if element_id:
            if element_id in self.ids:
                self.duplicates.add(element_id)
            self.ids.add(element_id)

def record(status, name, detail=""):
    RESULTS.append({
        "status": status,
        "name": name,
        "detail": str(detail),
    })

    suffix = f" | {detail}" if detail else ""
    print(f"[{status}] {name}{suffix}", flush=True)

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def run(name, function):
    try:
        result = function()
        detail = result if isinstance(result, str) else ""
        record("PASS", name, detail)
        return result

    except Exception as exc:
        record("FAIL", name, f"{type(exc).__name__}: {exc}")
        return None

def get(path, params=None):
    return SESSION.get(BASE_URL + path, params=params, timeout=(15, 90), allow_redirects=False)

def get_json(path, params=None):
    response = get(path, params=params)
    require(response.status_code == 200, f"{path} returned HTTP {response.status_code}")
    return response.json()

def verify_resource(path):
    response = get(path)
    require(response.status_code == 200, f"HTTP {response.status_code}")
    require(len(response.content) > 0, "Resource response was empty")
    return f"HTTP 200, {len(response.content)} bytes"

def verify_status(path, params, expected):
    response = get(path, params=params)
    require(response.status_code == expected, f"Expected HTTP {expected}, received {response.status_code}")
    return f"HTTP {expected}"

def parse_time(value):
    timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    require(timestamp.tzinfo is not None, "Prediction timestamp missing timezone")
    return timestamp.astimezone(timezone.utc)

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH - TC-012A")
    print("PRODUCTION DASHBOARD AND API SMOKE TESTS")
    print("=" * 50)
    print("Target:", BASE_URL)
    print("Environment: Render production")
    print("Mode: READ-ONLY")
    print()

    #Public homepage
    print("--- PAGE DELIVERY ---")

    def check_homepage():
        response = get("/")
        require(response.status_code == 200, f"Homepage returned HTTP {response.status_code}")
        require("<html" in response.text.lower(), "Homepage did not return HTML")
        return "HTTP 200, HTML received"

    run("Production homepage available", check_homepage)

    #Resident dashboard
    resident_html = None

    try:
        response = get("/resident")
        if response.status_code == 200:
            resident_html = response.text

            record("PASS", "Resident dashboard HTML delivered", "HTTP 200")

        elif response.status_code in {301, 302, 303, 307, 308, 401, 403}:
            record("SKIP", "Unauthenticated resident HTML inspection", (f"HTTP {response.status_code}; " "requires authenticated browser verification"))

        else:
            record("FAIL", "Resident dashboard HTML delivery", f"Unexpected HTTP {response.status_code}")

    except Exception as exc:
        record("FAIL", "Resident dashboard request", str(exc))

    if resident_html is not None:
        inspector = HTMLInspector()
        inspector.feed(resident_html)

        def check_required_ids():
            missing = REQUIRED_RESIDENT_IDS - inspector.ids
            require(not missing, f"Missing HTML IDs: {sorted(missing)}")
            return (f"{len(REQUIRED_RESIDENT_IDS)} " "required IDs present")

        run("Resident interactive HTML elements preserved", check_required_ids)
        run("Resident HTML contains no duplicate IDs", lambda: require(not inspector.duplicates, f"Duplicate IDs: {sorted(inspector.duplicates)}"))
        run("Resident skip-navigation link preserved", lambda: require('href="#main"' in resident_html, "Missing skip-to-content link"))

    #Static resources
    print("\n--- STATIC RESOURCES ---")
    resources = ["/static/css/resident.css", "/static/js/resident.js", "/static/js/place_search.js", "/static/images/logo.png"]

    for path in resources:
        run(f"Static resource available: {path}", lambda p=path: verify_resource(p))

    def check_accessibility_styles():
        stylesheet = get("/static/css/resident.css")
        require(stylesheet.status_code == 200, "Resident stylesheet unavailable")
        required = (":focus-visible", "prefers-reduced-motion")

        missing = [selector for selector in required if selector not in stylesheet.text]
        require(not missing, f"Missing accessibility rules: {missing}")
        return "Keyboard focus and reduced-motion rules found"

    run("Resident accessibility CSS rules present", check_accessibility_styles)

    #Production monitoring APIs
    print("\n--- MONITORING API ---")
    overview = run("Production overview API available", lambda: get_json("/api/overview"))
    locations = run("Production monitoring locations API available", lambda: get_json("/api/locations"))

    if isinstance(overview, dict):
        def check_overview():
            required = {
                "monitored_locations",
                "assessed_locations",
                "low",
                "moderate",
                "high",
                "latest_prediction"
            }

            missing = required - set(overview)
            require(not missing, f"Missing fields: {sorted(missing)}")
            require(overview["monitored_locations"] == EXPECTED_LOCATIONS, "Unexpected monitoring location count")
            require(overview["assessed_locations"] == EXPECTED_LOCATIONS, "Unexpected assessed location count")

            return "237 monitored and assessed locations"

        run("Overview contains correct monitoring totals", check_overview)

    if isinstance(locations, list):
        def check_location_coverage():
            ids = [point["location_id"] for point in locations]
            require(len(locations) == EXPECTED_LOCATIONS, f"Received {len(locations)} locations")
            require(len(set(ids)) == EXPECTED_LOCATIONS, "Duplicate location IDs detected")
            return "237 records, 237 unique location IDs"

        run("All 237 monitoring locations returned uniquely", check_location_coverage)

        def check_prediction_values():
            invalid = []

            for point in locations:
                try:
                    score = float(point["risk_score"])
                    level = str(point["risk_level"]).upper()
                    valid = (math.isfinite(score) and 0 <= score <= 1 and level in {"LOW", "MODERATE", "HIGH"})
                    parse_time(point["prediction_time"])

                    if not valid:
                        invalid.append(point["location_id"])

                except (KeyError, ValueError, TypeError, AssertionError):
                    invalid.append(point.get("location_id"))

            require(not invalid, f"Invalid records: {invalid[:10]}")
            return (f"{len(locations)} valid risk scores, categories and timestamps")

        run("Location predictions have valid values", check_prediction_values)

    if (isinstance(overview, dict) and isinstance(locations, list)):
        def check_overview_consistency():
            distribution = Counter(str(point["risk_level"]).upper() for point in locations)

            for category, key in (("LOW", "low"), ("MODERATE", "moderate"), ("HIGH", "high")):
                require(distribution[category] == overview[key], f"{category} count differs from overview")

            latest_api_time = max(parse_time(point["prediction_time"]) for point in locations)
            overview_time = parse_time(overview["latest_prediction"])
            require(latest_api_time == overview_time, "Latest location assessment differs from overview")
            age_hours = (datetime.now(timezone.utc) - overview_time).total_seconds() / 3600
            return ("Risk totals and timestamps agree; " f"measured assessment age: {age_hours:.2f}h")

        run("Overview agrees with all location records", check_overview_consistency)

    else:
        record("SKIP", "Overview/location cross-check", "Dependent API data unavailable")

    #Nairobi GIS files
    print("\n--- GIS BOUNDARIES ---")
    for label, path in (("Wards", "/static/gis/nairobi_wards.geojson"), ("Subcounties", "/static/gis/nairobi_subcounties.geojson")):
        def check_gis(p=path):
            data = get_json(p)
            require(data.get("type") == "FeatureCollection", "Not a GeoJSON FeatureCollection")
            features = data.get("features")
            require(isinstance(features, list) and len(features) > 0, "GIS layer contains no features")
            return f"{len(features)} GIS features loaded"

        run(f"{label} GeoJSON accessible and valid", check_gis)

    #Geographic search validation
    print("\n--- GEOGRAPHIC SEARCH ---")
    run("Short geographic search rejected", lambda: verify_status("/api/places/search", {"q": "ab"}, 422))
    run("Excessively long search rejected", lambda: verify_status("/api/places/search", {"q": "A" * 100}, 422))

    #Selected location history
    print("\n--- RESIDENT HISTORY API ---")
    if isinstance(locations, list) and locations:
        sorted_locations = sorted(locations, key=lambda point: int(point["location_id"]))
        sample_points = [sorted_locations[0], sorted_locations[len(sorted_locations) // 2], sorted_locations[-1]]

        for point in sample_points:
            location_id = point["location_id"]
            path = (f"/api/resident/locations/" f"{location_id}/history?days=30")

            try:
                response = get(path)
                if response.status_code in {301, 302, 303, 307, 308, 401, 403}:
                    record("SKIP", f"History for location {location_id}", "Authentication required")
                    continue

                require(response.status_code == 200, f"HTTP {response.status_code}")

                payload = response.json()
                predictions = payload.get("predictions")

                require(isinstance(predictions, list) and len(predictions) > 0, "Missing prediction history")
                record("PASS", f"History for location {location_id}", f"{len(predictions)} records")

            except Exception as exc:
                record("FAIL", f"History for location {location_id}", str(exc))

    #Results and evidence
    print("\n" + "=" * 50)
    counts = Counter(result["status"] for result in RESULTS)
    print("Passed:", counts["PASS"])
    print("Failed:", counts["FAIL"])
    print("Skipped:", counts["SKIP"])

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "test": "TC-012A",
                "target": BASE_URL,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "results": RESULTS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("Evidence:", OUTPUT_PATH)

    if counts["FAIL"] > 0:
        print("TC-012A RESULT: FAIL")
        sys.exit(1)

    if counts["SKIP"] > 0:
        print("TC-012A RESULT: AUTOMATED CHECKS PASS WITH SKIPS")
    else:
        print("TC-012A RESULT: PASS")

    print("NOTE: Browser interactions and authenticated dashboards remain to be tested.")

    print("=" * 50)

if __name__ == "__main__":
    main()
