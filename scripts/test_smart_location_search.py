from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app

def main():
    print("Nairobi FloodWatch Smart Location Search Tests")
    print("=" * 50)

    sample_result = {
        "name": "Example of a Nairobi Place",
        "display_name": "Example of a Nairobi Place, Nairobi, Kenya",
        "latitude": -1.30,
        "longitude": 36.81,
        "place_type": "residential",
        "ward_name": None,
        "ward_source": "Unavailable"
    }

    with TestClient(app) as client:
        #The API should reject incomplete search queries
        response = client.get("/api/places/search?q=ab")
        assert response.status_code == 422
        print("PASS: short geographic queries rejected")

        #verify the route and JSON structure without making an external request/ inventing actual GIS records
        with patch("src.dashboard.routes.places.search_nairobi_places", return_value=[sample_result]) as mocked_search:
            response = client.get("/api/places/search?q=Example%20Nairobi%20Place")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert len(payload["results"]) == 1
            assert payload["results"][0]["latitude"] == -1.30
            assert payload["results"][0]["ward_source"] == "Unavailable"

            mocked_search.assert_called_once()

            print("PASS: Geographic search endpoint responds correctly")
            print("PASS: Place coordinates and ward provenance preserved")

            #the endpoint should reject excessively long requests
            response = client.get("/api/places/search", params={"q": "A" * 100})
            assert response.status_code == 422
            print("PASS: Excessively long queries rejected")

        print()
        print("Smart location search backend tests: ALL PASSED")
        print("Note: External geocoding and map interactions require live testing")

if __name__ == "__main__":
    main()