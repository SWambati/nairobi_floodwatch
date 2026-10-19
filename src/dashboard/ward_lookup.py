import json
import logging
import os
import functools
from functools import lru_cache
from pathlib import Path

from config.settings import PROJECT_ROOT

logger = logging.getLogger("floodwatch.gis")

WARD_NAME_FIELDS = ("ward_name", "WARD_NAME", "ADM3_EN", "adm3_en", "Ward", "ward", "name", "Name",)

@lru_cache(maxsize=2)
def load_ward_boundaries(file_path: str):
    #GeoJSON coordinates must be in WGS84 (EPSG:4326)
    #cache geometrics so they are not parsed on every place search

    from shapely.geometry import shape

    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if data.get("type") != "FeatureCollection":
        raise ValueError("Ward GIS data must be a GeoJSON FeatureCollection")

    wards = []

    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        name = next(
            (
                str(properties[field]).strip()
                for field in WARD_NAME_FIELDS
                if properties.get(field)
            ),
            None,
        )
        geometry = feature.get("geometry")

        if not name or not geometry:
            continue

        polygon = shape(geometry)

        if not polygon.is_empty:
            wards.append((name, polygon))

    if not wards:
        raise ValueError("No named ward polygons found in GeoJSON")

    return wards

def identify_ward(latitude: float, longitude: float) -> dict:
    #do not derive a ward from nearest point name, a ward is verified only by the polygon containment

    configured_path = os.getenv("NAIROBI_WARDS_GEOJSON", "").strip()

    if not configured_path:
        return {"ward_name": None, "ward_source": "unavailable"}

    path = Path(configured_path)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if not path.is_file():
        logger.warning("Configured ward GeoJSON is unavailable")
        return {"ward_name": None, "ward_source": "unavailable"}

    try:
        from shapely.geometry import Point

        wards = load_ward_boundaries(str(path.resolve()))
        point = Point(longitude, latitude)

        for ward_name, polygon in wards:
            if polygon.covers(point):
                return {
                    "ward_name": ward_name,
                    "ward_source": "polygon"
                }

    except (OSError, ValueError, ImportError) as exc:
        logger.warning("Ward lookup unavailable: %s", exc)

        return {"ward_name": None, "ward_source": "unavailable"}

    return {"ward_name": None, "ward_source": "unverified"}
