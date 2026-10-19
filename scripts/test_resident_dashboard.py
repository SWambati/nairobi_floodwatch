from fastapi.testclient import TestClient
from app import app

def main():
    print("Nairobi FloodWatch Resident Dashboard Test")
    print("=" * 50)

    with TestClient(app) as client:
        # Verify that FastAPI serves the resident HTML page.
        page = client.get("/resident")
        assert page.status_code == 200, page.text
        assert "Your area, clearly understood" in page.text
        print("PASS: Resident HTML page served")

        # FastAPI exposes src/web/static through the /static URL prefix.
        # The logo is also used as the application's browser-tab icon.
        for path in (
            "/static/css/resident.css",
            "/static/js/resident.js",
            "/static/images/logo.png",
        ):
            response = client.get(path)
            assert response.status_code == 200, (f"Missing {path}: {response.status_code}")

        print("PASS: Resident CSS, JavaScript and logo")

        # Retrieve actual operational monitoring data from PostgreSQL.
        overview = client.get("/api/overview")
        locations = client.get("/api/locations")

        assert overview.status_code == 200, overview.text
        assert locations.status_code == 200, locations.text

        records = locations.json()

        assert records, "No operational monitoring points were returned"
        assert len(records) == overview.json()["monitored_locations"]

        print(f"PASS: {len(records):,} PostgreSQL monitoring locations")

        # Verify that historical predictions and weather observations can be retrieved for an existing monitoring point.
        location_id = records[0]["location_id"]

        history = client.get(f"/api/resident/locations/{location_id}/history?days=30")

        assert history.status_code == 200, history.text

        payload = history.json()

        assert payload["location_id"] == location_id
        assert isinstance(payload["predictions"], list)
        assert isinstance(payload["weather_readings"], list)

        print("PASS: Historical predictions and weather retrieval")

        # Unknown observation points must return HTTP 404.
        unknown = client.get("/api/resident/locations/2147483647/history")

        assert unknown.status_code == 404

        # Reject history ranges exceeding the API's supported limit.
        invalid = client.get(f"/api/resident/locations/{location_id}/history?days=9999")

        assert invalid.status_code == 422

        print("PASS: Invalid requests handled correctly")
        print()
        print("Resident dashboard backend tests: ALL PASSED")


if __name__ == "__main__":
    main()
