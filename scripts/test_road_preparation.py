import json
from pathlib import Path
import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
COUNTY_FILE = ROOT / "data" / "spatial" / "administrative" / "nairobi_county_adm1.geojson"
ROAD_DIR = ROOT / "data" / "spatial" / "roads"
ROAD_FILE = ROAD_DIR / "nairobi_drive_roads.gpkg"
METADATA_FILE = ROAD_DIR / "road_network_metadata.json"


def main():
    print("Nairobi FloodWatch Road Network Validation")
    print("=" * 50)

    assert ROAD_FILE.is_file(), f"Missing road file: {ROAD_FILE}"
    assert METADATA_FILE.is_file(), "Missing road network metadata"

    roads = gpd.read_file(ROAD_FILE, layer="roads")
    county = gpd.read_file(COUNTY_FILE).to_crs("EPSG:4326")

    assert not roads.empty
    assert roads.crs is not None
    assert roads.crs.to_epsg() == 4326

    print(f"PASS: {len(roads):,} road segments loaded")

    required = {
        "segment_id",
        "osm_way_id",
        "road_name",
        "road_class",
        "length_m",
        "geometry",
    }

    assert required.issubset(roads.columns)
    assert roads["segment_id"].is_unique
    assert roads["length_m"].gt(0).all()

    print("PASS: Road attributes and segment identifiers")

    assert roads.geometry.is_valid.all()
    assert not roads.geometry.is_empty.any()
    assert roads.geometry.geom_type.isin(["LineString", "MultiLineString"]).all()

    print("PASS: Road geometry validity")

    boundary = county.geometry.union_all()

    # Check a sample of segments against the actual county boundary.
    # A tiny buffer accounts for floating-point coordinate precision.
    for geometry in roads.geometry.iloc[:200]:
        assert boundary.buffer(1e-7).covers(geometry), ("Road segment lies outside the Nairobi boundary.")

    print("PASS: Sample road segments lie within Nairobi County")

    with METADATA_FILE.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    assert metadata["road_segments"] == len(roads)
    assert metadata["network_type"] == "drive"
    assert metadata["total_road_length_km"] > 0

    print("PASS: Network metadata and provenance")
    print()
    print("Road network validation: ALL PASSED")


if __name__ == "__main__":
    main()
