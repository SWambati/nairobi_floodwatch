import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import osmnx as ox


# Configuration
ROOT = Path(__file__).resolve().parents[1]
COUNTY_FILE = ROOT / "data" / "spatial" / "administrative" / "nairobi_county_adm1.geojson"
ROAD_DIR = ROOT / "data" / "spatial" / "roads"
ROAD_FILE = ROAD_DIR / "nairobi_drive_roads.gpkg"
METADATA_FILE = ROAD_DIR / "road_network_metadata.json"
CACHE_DIR = ROOT / "outputs" / "project_outputs" / "cache" / "osmnx"


def load_county():
    # Reuse the verified county boundary instead of asking a geocoder
    # to determine Nairobi's extent again.
    if not COUNTY_FILE.is_file():
        raise FileNotFoundError(COUNTY_FILE)

    county = gpd.read_file(COUNTY_FILE)

    if county.empty or county.crs is None:
        raise ValueError("County GIS data is empty or lacks a CRS.")

    county = county.to_crs("EPSG:4326")

    if not county.geometry.is_valid.all():
        raise ValueError("County boundary geometry is invalid.")

    polygon = county.geometry.union_all()

    if polygon.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("County geometry must be a polygon.")

    return polygon


def attribute_text(value, default=""):
    # OSMnx may return lists when several OSM ways are merged into
    # one simplified road edge.
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)

    if value is None or str(value).lower() == "nan":
        return default

    return str(value)


def build_road_network():
    county = load_county()

    ROAD_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Overpass responses are cached to avoid unnecessary downloads.
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR)
    ox.settings.requests_timeout = 240
    ox.settings.log_console = True

    print("Requesting Nairobi drivable roads from OpenStreetMap...")
    print("This first acquisition may take several minutes.")

    graph = ox.graph.graph_from_polygon(
        county,
        network_type="drive",
        simplify=True,
        retain_all=True,
        truncate_by_edge=False,
    )

    if graph.number_of_edges() == 0:
        raise ValueError("OpenStreetMap returned no road edges.")

    print(f"Network edges: {graph.number_of_edges():,}")

    # A driving network is directed. Convert it to an undirected
    # geometric representation so opposite driving directions do
    # not duplicate the same physical road for exposure analysis.
    undirected = ox.convert.to_undirected(graph)

    roads = ox.convert.graph_to_gdfs(
        undirected,
        nodes=False,
        edges=True,
        fill_edge_geometry=True,
    ).reset_index()

    roads = roads.to_crs("EPSG:4326")

    # Remove line portions outside Nairobi County.
    county_frame = gpd.GeoDataFrame(
        geometry=[county],
        crs="EPSG:4326",
    )

    roads = gpd.clip(roads, county_frame)

    roads = roads[
        roads.geometry.notna() &
        ~roads.geometry.is_empty &
        roads.geometry.geom_type.isin(
            ["LineString", "MultiLineString"]
        )
    ].copy().reset_index(drop=True)

    if roads.empty:
        raise ValueError("No roads remain after county clipping.")

    # Measure lengths in metres using Nairobi's UTM zone.
    metric = roads.to_crs("EPSG:32737")
    lengths = metric.geometry.length

    # Preserve the OSM identifier, road class, name and geometry.
    # Unnamed roads stay explicitly unnamed.
    output = gpd.GeoDataFrame(
        {
            "segment_id": [
                f"ROAD-{i:06d}"
                for i in range(1, len(roads) + 1)
            ],
            "osm_way_id": [
                attribute_text(value)
                for value in roads["osmid"]
            ],
            "road_name": [
                attribute_text(value, "Unnamed road")
                for value in roads.get(
                    "name",
                    ["Unnamed road"] * len(roads),
                )
            ],
            "road_class": [
                attribute_text(value, "unclassified")
                for value in roads.get(
                    "highway",
                    ["unclassified"] * len(roads),
                )
            ],
            "length_m": lengths.round(2).to_numpy(),
        },
        geometry=roads.geometry,
        crs="EPSG:4326",
    )

    # The GIS source is stored in a GeoPackage for spatial analysis.
    # We will produce a separate filtered web layer later.
    if ROAD_FILE.exists():
        ROAD_FILE.unlink()

    output.to_file(
        ROAD_FILE,
        layer="roads",
        driver="GPKG",
    )

    metadata = {
        "source": "OpenStreetMap",
        "source_url": "https://www.openstreetmap.org/",
        "license": "Open Database License (ODbL)",
        "acquired_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "network_type": "drive",
        "boundary_source": str(COUNTY_FILE),
        "road_segments": int(len(output)),
        "total_road_length_km": round(
            float(output["length_m"].sum()) / 1000,
            2,
        ),
        "crs": "EPSG:4326",
        "output_file": str(ROAD_FILE),
    }

    with METADATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)

    print(f"Road segments: {metadata['road_segments']:,}")
    print(f"Total road length: {metadata['total_road_length_km']:,.2f} km")
    print(f"GeoPackage: {ROAD_FILE}")
    print(f"Metadata: {METADATA_FILE}")


def main():
    parser = argparse.ArgumentParser(
        description="Prepare Nairobi's OpenStreetMap road network."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download and rebuild even when a GeoPackage exists.",
    )
    args = parser.parse_args()

    print("Nairobi FloodWatch Road Network Preparation")
    print("=" * 50)

    # Avoid downloading the same road network on every run.
    if ROAD_FILE.exists() and not args.refresh:
        print(f"Existing road network: {ROAD_FILE}")
        print("Use --refresh to rebuild.")
        return

    build_road_network()
    print()
    print("Road network preparation: OK")


if __name__ == "__main__":
    main()
