import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape


ROOT = Path(__file__).resolve().parents[2]

#path configuration
RASTER = ROOT / "data/spatial/hazard/icpac_kenya_100yr_flood_inundation_nairobi.tif"
ROADS = ROOT / "data/spatial/roads/nairobi_drive_roads.gpkg"
OUTPUT_DIR = ROOT / "outputs/project_outputs/processed/gis/roads"
WEB_DIR = ROOT / "src/web/static/gis"


METRIC_CRS = "EPSG:32737"
MIN_EXPOSED_LENGTH_M = 1.0


def load_positive_hazard_polygons(raster_path=RASTER):
    # The historical ETL divides positive ICPAC raster values by 20 to obtain the number of agreeing flood models (1 through 6).
    with rasterio.open(raster_path) as source:
        if source.count != 1 or source.crs is None:
            raise ValueError("Expected a single-band ICPAC raster with a valid CRS.")

        if source.tags(1).get("DESCRIPTION") != "100_YEAR_RP_INUNDATION":
            raise ValueError("Unexpected ICPAC band description. Verify the source raster.")

        band = source.read(1, masked=True)
        raw = np.asarray(band)
        valid = (~np.ma.getmaskarray(band) & np.isfinite(raw))

        # Zero and nodata are not interpreted as verified safe areas.
        positive = valid & (raw > 0)

        if not positive.any():
            raise ValueError("No positively mapped inundation cells were found.")

        scaled = raw[positive] / 20.0
        rounded = np.rint(scaled)

        if (not np.all(np.isclose(scaled, rounded, atol=1e-4, rtol=0)) or not np.all((rounded >= 1) & (rounded <= 6))):
            raise ValueError("ICPAC positive values must equal 20 times a flood-model agreement count between 1 and 6.")

        agreement = np.zeros(raw.shape, dtype="uint8")
        agreement[positive] = rounded.astype("uint8")

        # Polygonize only positive cells. Rasterio merges neighboring cells with equal values into contiguous polygons.
        polygons = [
            {
                "agreement_count": int(value),
                "geometry": shape(geometry),
            }
            for geometry, value in shapes(
                agreement,
                mask=positive,
                transform=source.transform,
            )
        ]

        statistics = {
            "total_pixels": int(raw.size),
            "valid_pixels": int(valid.sum()),
            "positive_pixels": int(positive.sum()),
            "zero_valued_pixels": int((valid & (raw == 0)).sum()),
            "nodata_pixels": int((~valid).sum()),
            "positive_pixels_by_agreement": {str(count): int((agreement == count).sum()) for count in range(1, 7)},
            "crs": str(source.crs),
            "pixel_resolution": [float(value) for value in source.res],
        }

        hazards = gpd.GeoDataFrame(polygons, geometry="geometry", crs=source.crs,)

    # Convert to a metric coordinate system so road lengths and geometric intersections can be measured appropriately.
    return hazards.to_crs(METRIC_CRS), statistics


def iter_lines(geometry):
    # A polygon-line intersection may yield a LineString, MultiLineString or GeometryCollection containing points.
    if geometry.is_empty:
        return

    if geometry.geom_type == "LineString":
        yield geometry

    elif geometry.geom_type in ("MultiLineString", "GeometryCollection",):
        for component in geometry.geoms:
            yield from iter_lines(component)


def compute_exposed_segments(roads_path=ROADS, raster_path=RASTER,):
    hazards, raster_statistics = load_positive_hazard_polygons(raster_path)

    roads = gpd.read_file(roads_path, layer="roads",)

    required = {
        "segment_id",
        "road_name",
        "road_class",
        "geometry",
    }

    missing = required - set(roads.columns)

    if missing or roads.crs is None:
        raise ValueError(f"Road data lacks a CRS or required fields: {missing}")

    if roads["segment_id"].duplicated().any():
        raise ValueError("Duplicate source road segment IDs.")

    roads = roads.to_crs(METRIC_CRS)
    roads = roads[roads.geometry.notna() & ~roads.geometry.is_empty].copy().reset_index(drop=True)

    if not roads.geometry.geom_type.isin(["LineString", "MultiLineString"]).all():
        raise ValueError("Road network contains non-line geometries.")

    # Spatial indexing reduces the number of expensive intersections.
    # Only roads that touch a positive hazard polygon are candidates.
    candidates = gpd.sjoin(roads, hazards, how="inner", predicate="intersects",)

    exposed = []

    for match in candidates.itertuples():
        road_line = roads.geometry.iloc[match.Index]
        hazard_area = hazards.geometry.iloc[match.index_right]
        intersection = road_line.intersection(hazard_area)

        # Preserve the intersecting road portion rather than assigning the hazard to the entire original road segment.
        for line in iter_lines(intersection):
            length = float(line.length)

            # Ignore tiny boundary-touch artifacts under one metre.
            if length < MIN_EXPOSED_LENGTH_M:
                continue

            exposed.append({
                "segment_id": str(match.segment_id),
                "road_name": (
                    str(match.road_name)
                    if match.road_name is not None
                    else "Unnamed road"),
                "road_class": (
                    str(match.road_class)
                    if match.road_class is not None
                    else "unclassified"
                ),
                "agreement_count": int(match.agreement_count),
                "length_m": round(length, 2),
                "geometry": line,
            })

    if not exposed:
        raise ValueError("No road geometry intersects positive ICPAC inundation cells.")

    segments = gpd.GeoDataFrame(exposed, geometry="geometry", crs=METRIC_CRS,)

    segments.insert(0, "exposure_id", [f"EXP-{number:07d}" for number in range(1, len(segments) + 1)],)

    run_statistics = {
        "roads_checked": len(roads),
        "candidate_intersections": len(candidates),
    }

    return segments, raster_statistics, run_statistics


def save_exposure(segments, raster_statistics, run_statistics, raster_path=RASTER, roads_path=ROADS, output_dir=OUTPUT_DIR, web_dir=WEB_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    web_dir.mkdir(parents=True, exist_ok=True)

    geopackage = (output_dir / "nairobi_road_inundation_exposure.gpkg")
    geojson = (web_dir / "nairobi_road_inundation_exposure.geojson")
    metadata_file = (output_dir / "road_exposure_metadata.json")

    # Preserve the full analytical geometries in a GeoPackage.
    if geopackage.exists():
        geopackage.unlink()

    segments.to_file(geopackage, layer="exposure", driver="GPKG",)

    # Make a separate display copy. Two-metre simplification reduces browser payload size without changing analysis results.
    browser = segments.copy()
    browser.geometry = browser.geometry.simplify(tolerance=2.0, preserve_topology=True,)
    browser.to_crs("EPSG:4326").to_file(geojson, driver="GeoJSON",)

    # Record source provenance for reproducibility.
    raster_sha256 = hashlib.sha256(Path(raster_path).read_bytes()).hexdigest()

    counts = Counter(int(value) for value in segments["agreement_count"])

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raster_source": str(raster_path),
        "raster_sha256": raster_sha256,
        "road_source": str(roads_path),
        "icpac_reference": (
            "https://geoportal.icpac.net/layers/"
            "geonode%3AKenya_flood_inundation"
        ),
        "encoding": ("Positive raster value divided by 20 equals the count of agreeing flood models (1-6)."),
        "interpretation": (
            "Road portions intersecting positive mapped inundation "
            "cells in a 100-year-return-period scenario. "
            "Not current flooding or road closures."
        ),
        "unknown_interpretation": (
            "Zero represents dry/no data in the source. "
            "Roads absent from this output are not verified safe."
        ),
        "minimum_saved_line_length_m": MIN_EXPOSED_LENGTH_M,
        "raster_statistics": raster_statistics,
        **run_statistics,
        "exposed_fragments": len(segments),
        "unique_source_road_segments": int(segments["segment_id"].nunique()),
        "total_exposed_length_km": round(float(segments.geometry.length.sum()) / 1000.0, 3, ),
        "fragments_by_agreement": {
            str(count): counts[count]
            for count in range(1, 7)
        },
        "geopackage": str(geopackage),
        "web_geojson": str(geojson),
        "web_geojson_size_mb": round(geojson.stat().st_size / (1024 * 1024), 3,),
    }

    metadata_file.write_text(json.dumps(report, indent=2), encoding="utf-8",)

    return report
