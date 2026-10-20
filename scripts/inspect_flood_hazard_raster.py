import argparse
import json
from pathlib import Path
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_window
from rasterio.mask import mask
from rasterio.warp import transform_geom
from shapely.geometry import mapping


# Configuration
ROOT = Path(__file__).resolve().parents[1]
COUNTY_FILE = ROOT / "data" / "spatial" / "administrative" / "nairobi_county_adm1.geojson"
REPORT_FILE = ROOT / "outputs" / "project_outputs" / "processed" / "gis" / "icpac_raster_inspection.json"
SEARCH_DIRS = [ROOT / "data", ROOT / "outputs"]
MAX_WINDOW_PIXELS = 15000000


def discover_raster():
    # Search project datasets only, never the virtual environment.
    matches = set()

    for directory in SEARCH_DIRS:
        if not directory.exists():
            continue

        for pattern in ("*.tif", "*.tiff"):
            for path in directory.rglob(pattern):
                description = str(path).lower()

                if "icpac" in description or "inundation" in description:
                    matches.add(path.resolve())

    matches = sorted(matches)

    if len(matches) != 1:
        print("Candidate ICPAC/inundation rasters:")

        for path in matches:
            print(f"  {path}")

        raise FileNotFoundError(
            f"Expected one raster, found {len(matches)}. "
            "Use --raster with the exact source path."
        )

    return matches[0]


def load_county():
    # Use the real county boundary acquired during historical GIS ETL.
    if not COUNTY_FILE.is_file():
        raise FileNotFoundError(COUNTY_FILE)

    county = gpd.read_file(COUNTY_FILE)

    if county.empty or county.crs is None:
        raise ValueError("County boundary is empty or lacks a CRS.")

    county = county.to_crs("EPSG:4326")

    if not county.geometry.is_valid.all():
        raise ValueError("County geometry is invalid.")

    return county.geometry.union_all()


def inspect_raster(path):
    county = load_county()

    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise ValueError("Hazard raster does not declare its CRS.")

        # Transform the county polygon into the raster's coordinate system.
        geometry = transform_geom(
            "EPSG:4326",
            dataset.crs,
            mapping(county),
        )

        report = {
            "source_file": str(path),
            "driver": dataset.driver,
            "crs": str(dataset.crs),
            "width": dataset.width,
            "height": dataset.height,
            "band_count": dataset.count,
            "dtype": list(dataset.dtypes),
            "nodata": dataset.nodata,
            "pixel_resolution": list(dataset.res),
            "bounds": list(dataset.bounds),
            "band_descriptions": list(dataset.descriptions),
            "scales": list(dataset.scales),
            "offsets": list(dataset.offsets),
            "dataset_tags": dataset.tags(),
            "band_1_tags": dataset.tags(1),
        }

        if dataset.count < 1:
            raise ValueError("Raster contains no data bands.")

        # Estimate the Nairobi window size before allocating memory.
        try:
            window = geometry_window(
                dataset,
                [geometry],
            )
        except rasterio.errors.WindowError as exc:
            raise ValueError(
                "Nairobi County does not overlap the raster."
            ) from exc

        pixels = int(window.width * window.height)
        report["nairobi_window_pixels"] = pixels

        if pixels > MAX_WINDOW_PIXELS:
            report["statistics_status"] = "skipped_large_window"
            report["inspection_note"] = (
                "Nairobi raster window exceeds the memory safety limit."
            )
            return report

        # A masked array keeps nodata/outside-county pixels separate.
        clipped, _ = mask(
            dataset,
            [geometry],
            indexes=1,
            crop=True,
            filled=False,
        )

        values = clipped.compressed()
        values = values[np.isfinite(values)]

        if values.size == 0:
            raise ValueError(
                "No valid raster values exist inside Nairobi County."
            )

        report["statistics_status"] = "complete"
        report["valid_pixel_count"] = int(values.size)
        report["minimum"] = float(values.min())
        report["maximum"] = float(values.max())
        report["mean"] = float(values.mean())
        report["percentiles"] = {
            "p25": float(np.percentile(values, 25)),
            "p50": float(np.percentile(values, 50)),
            "p75": float(np.percentile(values, 75)),
            "p95": float(np.percentile(values, 95)),
        }

        # These are numeric diagnostics, NOT interpreted hazard classes.
        report["zero_pixels"] = int(np.count_nonzero(values == 0))
        report["positive_pixels"] = int(np.count_nonzero(values > 0))
        report["negative_pixels"] = int(np.count_nonzero(values < 0))

        # Inspect a manageable sample to see whether values resemble
        # discrete classes or a continuous quantity.
        sampled = values[::max(1, len(values) // 100000)]
        unique, counts = np.unique(sampled, return_counts=True)

        report["sample_unique_value_count"] = len(unique)

        if len(unique) <= 20:
            report["sample_values"] = [
                {"value": float(value), "count": int(count)}
                for value, count in zip(unique, counts)
            ]
        else:
            report["sample_values"] = [
                {"value": float(value), "count": int(count)}
                for value, count in sorted(
                    zip(unique, counts),
                    key=lambda item: item[1],
                    reverse=True,
                )[:15]
            ]

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Inspect the ICPAC flood-hazard raster."
    )
    parser.add_argument("--raster", type=Path)
    args = parser.parse_args()

    print("Nairobi FloodWatch ICPAC Raster Inspection")
    print("=" * 50)

    raster_path = (
        args.raster.resolve()
        if args.raster
        else discover_raster()
    )

    if not raster_path.is_file():
        raise FileNotFoundError(raster_path)

    report = inspect_raster(raster_path)

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_FILE.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print(f"Raster: {raster_path.name}")
    print(f"CRS: {report['crs']}")
    print(f"Bands: {report['band_count']}")
    print(f"Nodata: {report['nodata']}")
    print(f"Pixel resolution: {report['pixel_resolution']}")
    print(f"Inspection status: {report['statistics_status']}")

    if report["statistics_status"] == "complete":
        print(f"Valid Nairobi pixels: {report['valid_pixel_count']:,}")
        print(f"Minimum: {report['minimum']}")
        print(f"Maximum: {report['maximum']}")
        print(f"Median: {report['percentiles']['p50']}")
        print(f"Zero-valued pixels: {report['zero_pixels']:,}")
        print(f"Positive-valued pixels: {report['positive_pixels']:,}")
        print(f"Distinct sampled values: {report['sample_unique_value_count']}")
        print(f"Sample values: {report['sample_values']}")

    print(f"Report: {REPORT_FILE}")
    print()
    print("ICPAC inspection: OK")
    print("Hazard-value interpretation: NOT YET VERIFIED")


if __name__ == "__main__":
    main()
