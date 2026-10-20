import json
from pathlib import Path
import geopandas as gpd
from fastapi.testclient import TestClient
from shapely.geometry import shape
from app import app

from src.gis.road_exposure import (OUTPUT_DIR, RASTER, ROADS, WEB_DIR, load_positive_hazard_polygons)

#path configuration
GEOPACKAGE = OUTPUT_DIR / "nairobi_road_inundation_exposure.gpkg"
METADATA = OUTPUT_DIR / "road_exposure_metadata.json"
GEOJSON = WEB_DIR / "nairobi_road_inundation_exposure.geojson"


def main():
    print("Nairobi FloodWatch Road Exposure Validation")
    print("=" * 50)

    for path in (RASTER, ROADS, GEOPACKAGE, METADATA, GEOJSON):
        assert path.is_file(), f"Required GIS file missing: {path}"

    roads = gpd.read_file(ROADS, layer="roads")
    exposed = gpd.read_file(GEOPACKAGE, layer="exposure")

    assert not roads.empty
    assert not exposed.empty

    print(f"PASS: {len(roads):,} roads and {len(exposed):,} exposure fragments")

    # Retained geometries should be nonempty lines with real lengths.
    assert exposed.geometry.notna().all()
    assert exposed.geometry.is_valid.all()
    assert not exposed.geometry.is_empty.any()
    assert exposed.geometry.geom_type.isin(["LineString", "MultiLineString"]).all()
    assert exposed["length_m"].ge(1).all()
    assert exposed["exposure_id"].is_unique

    print("PASS: Exposure geometry and identifiers")

    # Agreement counts come from the historical raw / 20 conversion.
    assert exposed["agreement_count"].between(1, 6).all()

    original_ids = set(roads["segment_id"].astype(str))

    assert set(exposed["segment_id"].astype(str)).issubset(original_ids)

    print("PASS: Agreement classes and source road references")

    # Verify that sample output segments genuinely overlap positive ICPAC polygons of their assigned agreement count.
    hazards, raster_stats = load_positive_hazard_polygons(RASTER)

    hazard_groups = {
        count: hazards[
            hazards["agreement_count"] == count
        ].geometry.union_all()
        for count in range(1, 7)
        if (hazards["agreement_count"] == count).any()
    }

    metric_exposed = exposed.to_crs(hazards.crs)

    for item in metric_exposed.head(100).itertuples():
        count = int(item.agreement_count)
        polygon = hazard_groups[count]

        intersection = item.geometry.intersection(polygon)

        assert intersection.length > 0, (f"Exposure fragment {item.exposure_id} does not overlap its classified ICPAC area.")

        # Allow a small numerical tolerance for reprojection.
        assert item.geometry.difference(polygon).length < 0.5, (f"Fragment {item.exposure_id} extends outside its mapped agreement area.")

    print("PASS: Sample exposed road fragments match ICPAC geometry")

    report = json.loads(METADATA.read_text(encoding="utf-8"))

    assert report["exposed_fragments"] == len(exposed)
    assert report["roads_checked"] == len(roads)
    assert report["raster_statistics"] == raster_stats
    assert report["total_exposed_length_km"] > 0

    print("PASS: Metadata and processing provenance")

    # Check the complete GeoJSON rather than trusting its filename.
    web_data = json.loads(GEOJSON.read_text(encoding="utf-8"))

    assert web_data["type"] == "FeatureCollection"
    assert len(web_data["features"]) == len(exposed)

    for feature in web_data["features"][:100]:
        assert 1 <= int(feature["properties"]["agreement_count"]) <= 6

        geometry = shape(feature["geometry"])

        assert not geometry.is_empty
        assert geometry.geom_type in ("LineString", "MultiLineString")

    print("PASS: Browser GeoJSON validity")

    with TestClient(app) as client:
        response = client.get("/static/gis/nairobi_road_inundation_exposure.geojson")

        assert response.status_code == 200, response.text
        assert len(response.json()["features"]) == len(exposed)

    print("PASS: FastAPI serves road-exposure GIS data")
    print()
    print("Road exposure tests: ALL PASSED")


if __name__ == "__main__":
    main()
