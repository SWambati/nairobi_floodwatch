import argparse
import time
from pathlib import Path
from src.gis.road_exposure import (RASTER, ROADS, compute_exposed_segments, save_exposure)

def main():
    parser = argparse.ArgumentParser(description="Calculate mapped Nairobi road inundation exposure.")
    parser.add_argument("--raster", type=Path, default=RASTER,)
    parser.add_argument("--roads", type=Path, default=ROADS,)

    args = parser.parse_args()

    raster_path = args.raster.resolve()
    roads_path = args.roads.resolve()

    print("Nairobi FloodWatch Road Exposure Processing")
    print("=" * 50)

    if not raster_path.is_file():
        raise FileNotFoundError(f"ICPAC raster not found: {raster_path}")

    if not roads_path.is_file():
        raise FileNotFoundError(f"Road network not found: {roads_path}")

    started = time.perf_counter()

    print("Loading ICPAC raster and Nairobi roads...")
    print(f"Raster: {raster_path.name}")
    print(f"Road network: {roads_path.name}")
    print()

    # Spatially intersect actual road geometry with positive flood-model agreement cells from the verified ICPAC raster.
    segments, raster_stats, run_stats = compute_exposed_segments(roads_path=roads_path, raster_path=raster_path,)

    print(f"Original road segments checked: {run_stats['roads_checked']:,}")
    print(f"Candidate intersections: {run_stats['candidate_intersections']:,}")
    print(f"Mapped exposure fragments: {len(segments):,}")
    print()

    # Save the analytical GeoPackage, browser GeoJSON and provenance.
    report = save_exposure(segments, raster_stats, run_stats, raster_path=raster_path, roads_path=roads_path,)

    print("Flood-model agreement distribution:")

    for count, fragments in report["fragments_by_agreement"].items():
        print(f"  {count} of 6 models: {fragments:,} road fragments")

    print()
    print(f"Unique intersecting source roads: {report['unique_source_road_segments']:,}")
    print(f"Total mapped overlapping length: {report['total_exposed_length_km']:,.3f} km")
    print(f"Web GeoJSON size: {report['web_geojson_size_mb']:.3f} MB")

    elapsed = time.perf_counter() - started

    print(f"Processing time: {elapsed:.2f} seconds")
    print()
    print(f"GeoPackage: {report['geopackage']}")
    print(f"GeoJSON: {report['web_geojson']}")
    print()

    print("Road exposure processing: OK")


if __name__ == "__main__":
    main()
