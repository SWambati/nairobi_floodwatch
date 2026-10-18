from fastapi.testclient import TestClient
from app import app

def main():
    print("Nairobi FloodWatch FastAPI Foundation Test")
    print("=" * 50)

    with TestClient(app) as client:
        #confirm that homepage is being successfully served

        homepage = client.get("/")
        assert homepage.status_code == 200
        assert "Nairobi FloodWatch" in homepage.text
        print("PASS: HTML homepage")

        #confirm CSS and JS are available through FastAPI
        css = client.get("/static/css/main.css")
        javascript = client.get("/static/js/app.js")

        assert css.status_code == 200
        assert javascript.status_code == 200
        print("PASS: Static CSS and JavaScript")

        #connect to PostgreSQL database using the API
        health = client.get("/api/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok"
        print("PASS: PostgreSQL health endpoint")

        #validate citywide monitoring counts
        overview_response = client.get("/api/overview")
        assert overview_response.status_code == 200, overview_response.text
        overview = overview_response.json()
        print("PASS: Citywide monitoring overview")

        #verify that point-level records are returned
        locations_reponse = client.get("/api/locations")
        assert locations_reponse.status_code == 200, locations_reponse.text

        locations = locations_reponse.json()
        assert isinstance(locations, list)
        assert len(locations) > 0

        location_ids = [row["location_id"] for row in locations]
        assert len(set(location_ids)) == len(location_ids)
        assert overview["monitored_locations"] == len(locations)

        print(f"PASS: {len(locations):,} unique observation points reviewed")

        #every assessed point must have one valid risk classification
        assessed_count = sum(row["risk_level"] in {"LOW", "MODERATE", "HIGH"} for row in locations)

        assert assessed_count == overview["assessed_locations"]

        risk_count = (overview["low"] +  overview["moderate"] + overview["high"])

        assert risk_count == assessed_count
        print("PASS: Risk categories and summary counts are consistent")

        #confirm that a location can be retrieved individually
        location_id = locations[0]["location_id"]

        detail_response = client.get(f"/api/locations/{location_id}")
        assert detail_response.status_code == 200, detail_response.text
        assert detail_response.json()["location_id"] == location_id

        print("PASS: Individual Location details")

        #an unkown location should not produce fabricated records
        not_found = client.get("/api/locations/2147483647")
        assert not_found.status_code == 404

        print("PASS: Unknown location returns HTTP 404")

    print()
    print("FastAPI foundation tests: ALL PASSED")

if __name__ == "__main__":
    main()



