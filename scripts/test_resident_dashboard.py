
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from app import app


# Test structural contracts, not exact headings that may change in UI reviews.
REQUIRED_IDS = {
    "main", "freshness", "errorNotice", "locTitle", "useLocation",
    "chooseArea", "clearLocation", "locationStatus", "areaSearch",
    "riskFilter", "residentMap", "pointCount", "areaHeading", "areaTitle",
    "areaRisk", "areaScore", "scoreMeaning", "assessmentTime", "assessmentAge",
    "temperature", "humidity", "rainfall", "weatherTime", "nearbyList",
    "advisoryStatus", "riskHistoryChart", "weatherHistoryChart",
    "downloadHistory", "geoSearchPanel", "geoSearchButton",
    "geoSearchLoading", "geoMatches", "geoContext", "emergencyTitle",
}


class ElementInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.duplicates = set()

    def handle_starttag(self, tag, attrs):
        element_id = dict(attrs).get("id")
        if element_id:
            if element_id in self.ids:
                self.duplicates.add(element_id)
            self.ids.add(element_id)


def main():
    print("Nairobi FloodWatch Resident Dashboard Test")
    print("=" * 50)

    with TestClient(app) as client:
        # Verify that the page exists and preserves all JavaScript hooks.
        page = client.get("/resident")
        assert page.status_code == 200, page.text

        inspector = ElementInspector()
        inspector.feed(page.text)
        assert not inspector.duplicates, f"Duplicate HTML IDs: {inspector.duplicates}"
        missing = REQUIRED_IDS - inspector.ids
        assert not missing, f"Missing resident element IDs: {sorted(missing)}"
        assert 'href="#main"' in page.text
        print("PASS: Resident HTML and interactive element IDs")

        # Confirm that the app serves every asset needed by the resident UI.
        for path in (
            "/static/css/resident.css",
            "/static/js/resident.js",
            "/static/js/place_search.js",
            "/static/images/logo.png",
        ):
            response = client.get(path)
            assert response.status_code == 200, (
                f"Missing {path}: {response.status_code}"
            )
        print("PASS: Resident stylesheet, JavaScript and logo")

        # Verify the actual PostgreSQL-backed monitoring API responses.
        overview = client.get("/api/overview")
        locations = client.get("/api/locations")
        assert overview.status_code == 200, overview.text
        assert locations.status_code == 200, locations.text

        records = locations.json()
        assert records, "No operational observation points returned"
        assert len(records) == overview.json()["monitored_locations"]
        assert len({row["location_id"] for row in records}) == len(records)
        print(f"PASS: {len(records):,} unique PostgreSQL observation points")

        # Historical endpoint returns recorded, rather than invented, values.
        location_id = records[0]["location_id"]
        history = client.get(
            f"/api/resident/locations/{location_id}/history?days=30"
        )
        assert history.status_code == 200, history.text

        payload = history.json()
        assert payload["location_id"] == location_id
        assert isinstance(payload["predictions"], list)
        assert isinstance(payload["weather_readings"], list)
        print("PASS: Recorded prediction and weather history")

        # Unknown monitoring points and invalid ranges must be rejected.
        unknown = client.get("/api/resident/locations/2147483647/history")
        invalid = client.get(
            f"/api/resident/locations/{location_id}/history?days=9999"
        )
        assert unknown.status_code == 404
        assert invalid.status_code == 422
        print("PASS: Unknown locations and invalid ranges rejected")

    print()
    print("Resident dashboard backend tests: ALL PASSED")


if __name__ == "__main__":
    main()
