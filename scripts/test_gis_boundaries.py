import json
import os
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from shapely.geometry import shape
from app import app

from src.dashboard.ward_lookup import identify_ward

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIS_DIR = PROJECT_ROOT / "src" / "web" / "static" / "gis"

LAYERS = {
    "wards": {
        "filename": "nairobi_wards.geojson",
        "expected": 85,
        "name_field": "ward_name",
    },
    "subcounties": {
        "filename": "nairobi_subcounties.geojson",
        "expected": 17,
        "name_field": "subcounty_name",
    },
}


def validate_geojson(level, settings):
    path = GIS_DIR / settings["filename"]

    if not path.is_file():
        raise FileNotFoundError(f"Missing {level} GIS file: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == settings["expected"]

    for feature in data["features"]:
        properties = feature.get("properties") or {}
        name = properties.get(settings["name_field"])
        assert isinstance(name, str) and name.strip(), (f"Missing administrative name in {level}.")

        geometry = shape(feature["geometry"])

        assert not geometry.is_empty
        assert geometry.is_valid
        assert geometry.geom_type in {
            "Polygon",
            "MultiPolygon",
        }

        # Sanity-check WGS84 Nairobi longitude/latitude coordinates.
        representative = geometry.representative_point()
        assert 35 < representative.x < 38
        assert -3 < representative.y < 1

    return data


def validate_ward_lookup(data):
    # Check an interior point of a real ward polygon rather
    # than hardcoding unverified test coordinates.
    sample = data["features"][0]
    geometry = shape(sample["geometry"])
    representative = geometry.representative_point()

    path = GIS_DIR / "nairobi_wards.geojson"

    with patch.dict(os.environ, {"NAIROBI_WARDS_GEOJSON": str(path)},):
        result = identify_ward(
            latitude=representative.y,
            longitude=representative.x,
        )

    assert result["ward_source"] == "polygon"
    assert result["ward_name"], ("Ward containment did not return a name.")

    print("PASS: Actual ward polygon containment")


def main():
    print("Nairobi FloodWatch GIS Integration Tests")
    print("=" * 50)

    datasets = {}

    for level, settings in LAYERS.items():
        datasets[level] = validate_geojson(level, settings,)

        print(f"PASS: {settings['expected']} valid {level} polygons")

    validate_ward_lookup(datasets["wards"])

    with TestClient(app) as client:
        # Validate that the browser can retrieve the prepared GIS files.
        for level, settings in LAYERS.items():
            url = f"/static/gis/{settings['filename']}"
            response = client.get(url)

            assert response.status_code == 200, (f"{url}: HTTP {response.status_code}")

            assert (len(response.json()["features"]) == settings["expected"])

            print(f"PASS: {level} served by FastAPI")

        # Confirm the new layer controls exist on the resident page.
        response = client.get("/resident")

        assert response.status_code == 200

        for element_id in (
            "toggleObservations",
            "toggleWards",
            "toggleSubcounties",
            "gisLayerStatus",
            "residentMap",
        ):
            assert f'id="{element_id}"' in response.text

        assert "/static/js/gis_layers.js" in response.text
        gis_script = client.get("/static/js/gis_layers.js")
        assert gis_script.status_code == 200

        print("PASS: GIS controls and JavaScript available")

    print()
    print("GIS integration tests: ALL PASSED")


if __name__ == "__main__":
    main()
