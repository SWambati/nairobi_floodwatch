import argparse
from pathlib import Path
import geopandas as gpd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIS_OUTPUT_DIR = PROJECT_ROOT / "src" / "web" / "static" / "gis"
SOURCE_FILES = {
    "wards": "nairobi_wards_adm3.geojson",
    "subcounties": "nairobi_subcounties_adm2.geojson",
}

EXPECTED_COUNTS = {
    "wards": 85,
    "subcounties": 17,
}

NAME_FIELDS = {
    "wards": [
        "ward_name", "ADM3_EN", "ADM3_NAME",
        "NAME_3", "shapeName", "Ward",
        "ward", "name", "Name",
    ],
    "subcounties": [
        "subcounty_name", "ADM2_EN", "ADM2_NAME",
        "NAME_2", "shapeName", "Subcounty",
        "subcounty", "name", "Name",
    ],
}

PARENT_FIELDS = [
    "subcounty_name", "ADM2_EN", "ADM2_NAME",
    "NAME_2", "subcounty",
]


def discover_source(filename: str) -> Path:
    # Locate the actual historical GIS file
    # Do not automatically substitute external administrative data.
    roots = [
        PROJECT_ROOT / "data",
        PROJECT_ROOT / "outputs",
        PROJECT_ROOT / "src" / "gis",
    ]

    matches = set()

    for root in roots:
        if root.exists():
            matches.update(root.rglob(filename))

    if len(matches) != 1:
        found = "\n".join(str(p) for p in sorted(matches))
        raise FileNotFoundError(
            f"Expected one source file named {filename}, "
            f"found {len(matches)}.\n{found}\n"
            "Provide its path using --wards or --subcounties."
        )

    return next(iter(matches))


def find_field(columns, candidates):
    # GIS providers use different names for administrative attributes.
    return next(
        (name for name in candidates if name in columns),
        None,
    )


def prepare_layer(source: Path, level: str) -> Path:
    boundaries = gpd.read_file(source)

    if boundaries.empty:
        raise ValueError(f"{level} source is empty: {source}")

    if boundaries.crs is None:
        raise ValueError(
            f"{level} source has no CRS. "
            "Verify its coordinate system before continuing."
        )

    expected = EXPECTED_COUNTS[level]

    if len(boundaries) != expected:
        raise ValueError(
            f"Expected {expected} {level} polygons, "
            f"found {len(boundaries)}."
        )

    if boundaries.geometry.isna().any():
        raise ValueError(f"{level} contains missing geometries.")

    if boundaries.geometry.is_empty.any():
        raise ValueError(f"{level} contains empty geometries.")

    if not boundaries.geometry.is_valid.all():
        raise ValueError(
            f"{level} contains invalid geometries. "
            "Inspect and repair the original GIS source first."
        )

    if not boundaries.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError(f"{level} must contain only polygon geometries.")

    name_field = find_field(
        boundaries.columns,
        NAME_FIELDS[level],
    )

    if name_field is None:
        raise ValueError(
            f"Could not identify the {level} name column. "
            f"Available columns: {list(boundaries.columns)}"
        )

    output_field = (
        "ward_name" if level == "wards"
        else "subcounty_name"
    )

    # Keep only administrative attributes needed by the application.
    selected = [name_field, "geometry"]
    parent_field = None

    if level == "wards":
        parent_field = find_field(
            boundaries.columns,
            PARENT_FIELDS,
        )

        if parent_field and parent_field not in selected:
            selected.insert(1, parent_field)

    prepared = boundaries[selected].copy()
    prepared = prepared.rename(columns={name_field: output_field})

    if parent_field and parent_field != name_field:
        prepared = prepared.rename(columns={parent_field: "subcounty_name"})

    prepared[output_field] = (prepared[output_field].astype("string").str.strip())

    if prepared[output_field].isna().any():
        raise ValueError(f"{level} contains missing names.")

    if prepared[output_field].eq("").any():
        raise ValueError(f"{level} contains blank names.")

    # Convert to WGS84 longitude/latitude for Leaflet and browser GIS.
    # We deliberately preserve the original polygon shapes.
    prepared = prepared.to_crs("EPSG:4326")

    min_x, min_y, max_x, max_y = prepared.total_bounds

    # A basic geographic sanity check prevents accidentally publishing
    # coordinates from another country or an untransformed local CRS.
    if not (
        35 < min_x < 38 and
        35 < max_x < 38 and
        -3 < min_y < 1 and
        -3 < max_y < 1
    ):
        raise ValueError(
            f"{level} geographical bounds look incorrect: "
            f"{prepared.total_bounds}"
        )

    GIS_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_name = (
        "nairobi_wards.geojson"
        if level == "wards"
        else "nairobi_subcounties.geojson"
    )

    output_path = GIS_OUTPUT_DIR / output_name

    prepared.to_file(
        output_path,
        driver="GeoJSON",
    )

    # Read the exported file back to confirm it survived serialization.
    verified = gpd.read_file(output_path)

    if len(verified) != expected:
        raise RuntimeError(
            f"{level} feature count changed during export."
        )

    print(f"{level.title()}: {len(verified):,} polygons")
    print(f"Source: {source}")
    print(f"Output: {output_path}")
    print(f"Size: {output_path.stat().st_size / 1024:.1f} KB")
    print(f"CRS: {verified.crs}")
    print()

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Prepare Nairobi administrative GIS layers."
    )

    parser.add_argument("--wards", type=Path)
    parser.add_argument("--subcounties", type=Path)

    args = parser.parse_args()

    print("Nairobi FloodWatch Administrative GIS Preparation")
    print("=" * 54)

    for level in SOURCE_FILES:
        specified = getattr(args, level)

        if specified:
            source = specified.resolve()
        else:
            source = discover_source(SOURCE_FILES[level])

        if not source.is_file():
            raise FileNotFoundError(source)

        prepare_layer(source, level)

    print("Administrative GIS preparation: OK")


if __name__ == "__main__":
    main()
