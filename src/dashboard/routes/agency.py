import json
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import (APIRouter, Depends, HTTPException, Query)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.auth.authorization import require_agency_access
from src.operational.persistence import create_database_engine

#LOGGING AND API ROUTER
logger = logging.getLogger("floodwatch.agency")
router = APIRouter(prefix="/api/agency", tags=["Agency Monitoring"], dependencies=[Depends(require_agency_access)], include_in_schema=False)

#TIMEZONE AND GIS CONFIGURATION
NAIROBI_TZ = ZoneInfo("Africa/Nairobi")
GIS_DIRECTORY = (Path(__file__).resolve().parents[2] / "web" / "static" / "gis")
GIS_LAYERS = {
    "ward": ("nairobi_wards.geojson", "ward_name"),
    "subcounty": ("nairobi_subcounties.geojson", "subcounty_name"),
}

#DATABASE CONNECTION
@lru_cache(maxsize=1)
def get_engine():
    #Reuse the application's existing PostgreSQL engine.
    return create_database_engine()

#GEOGRAPHIC NAME NORMALIZATION
def normalize_name(value):
    #Normalize administrative area names for comparisons.
    return " ".join(str(value or "").split()).casefold()

#LOAD GIS BOUNDARIES
@lru_cache(maxsize=2)
def load_boundary_features(layer):
    #Load ward or subcounty GeoJSON features.
    #Results are cached to avoid repeatedly loading the same files for every trends request.
    filename, property_name = GIS_LAYERS[layer]
    path = GIS_DIRECTORY / filename

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

        if data.get("type") != "FeatureCollection":
            raise ValueError("Expected a GeoJSON FeatureCollection.")

        features = []

        for feature in data.get("features", []):
            properties = (feature.get("properties") or {})
            geometry = (feature.get("geometry") or {})
            name = normalize_name(properties.get(property_name))

            if not name:
                continue

            if geometry.get("type") not in {"Polygon","MultiPolygon"}:
                continue

            features.append((name, geometry))

        if not features:
            raise ValueError("No usable boundary features.")

        return tuple(features)

    except (OSError, ValueError, TypeError) as exc:
        logger.exception("Unable to load GIS boundary layer: %s", layer)
        raise HTTPException(status_code=503, detail=("Geographic boundary data is unavailable.")) from exc

#POINT-IN-POLYGON TEST
def ring_contains_point(ring, longitude, latitude):
    #Determine whether a geographic coordinate lies inside a GeoJSON polygon ring.
    #Uses ray casting.
    #Boundary points are treated as included.
    if len(ring) < 3:
        return False

    inside = False

    for index in range(len(ring)):
        previous = ring[index - 1]
        current = ring[index]

        ax = float(previous[0])
        ay = float(previous[1])

        bx = float(current[0])
        by = float(current[1])

        cross = ((longitude - ax) * (by - ay) - (latitude - ay) * (bx - ax))

        # Check whether the coordinate falls directly on a polygon edge.
        if abs(cross) < 1e-10:
            if (min(ax, bx) - 1e-10 <= longitude <= max(ax, bx) + 1e-10 and min(ay, by) - 1e-10 <= latitude <= max(ay, by) + 1e-10):
                return True

        # Ray-casting intersection.
        if (ay > latitude) != (by > latitude):
            crossing_x = (ax + (latitude - ay) * (bx - ax) / (by - ay))

            if longitude < crossing_x:
                inside = not inside

    return inside


#POLYGON AND MULTIPOLYGON MATCHING
def geometry_contains_point(geometry, longitude, latitude):
    #Support GeoJSON Polygon and MultiPolygon geometry.
    # Polygon holes are excluded.
    kind = geometry.get("type")
    coordinates = (geometry.get("coordinates") or [])

    if kind == "Polygon":
        polygons = [coordinates]

    elif kind == "MultiPolygon":
        polygons = coordinates

    else:
        return False

    for polygon in polygons:
        if not polygon:
            continue

        outer_ring = polygon[0]
        holes = polygon[1:]

        if not ring_contains_point(outer_ring, longitude, latitude):
            continue

        if any(ring_contains_point(hole, longitude, latitude) for hole in holes):
            continue

        return True

    return False

#VALIDATE ADMINISTRATIVE AREA SELECTIONS
def selected_boundaries(layer, selected_name):
    #Resolve a selected ward/subcounty name to its matching GeoJSON polygon geometry.
    #Unknown selections are rejected rather than silently returning misleading area counts.
    if (not selected_name or normalize_name(selected_name) == "all"):
        return None

    requested = normalize_name(selected_name)
    matches = [geometry for name, geometry in load_boundary_features(layer) if name == requested]

    if not matches:
        raise HTTPException(status_code=422, detail=(f"Unknown {layer} boundary selection."))

    return matches

#CHECK LOCATION AGAINST SELECTED BOUNDARIES
def point_matches_boundaries(latitude, longitude, selections):
    #A monitoring point must belong to every selected administrative boundary.
    #No administrative filter means every location is eligible for aggregation.
    for geometries in selections:
        if geometries is None:
            continue

        if not any(geometry_contains_point(geometry, longitude, latitude) for geometry in geometries):
            return False

    return True

#HISTORICAL PREDICTION QUERY
DAILY_PREDICTIONS_SQL = text("""
    WITH candidates AS (
        SELECT
            p.prediction_id,
            p.location_id,
            p.prediction_time,
            p.created_at,
            p.risk_level,

            l.latitude,
            l.longitude,

            (
                p.prediction_time
                AT TIME ZONE 'Africa/Nairobi'
            )::date AS local_date

        FROM flood_risk_prediction AS p

        JOIN location AS l
            ON l.location_id = p.location_id

        WHERE p.prediction_time >= :start_time
          AND p.prediction_time < :end_time
    ),

    ranked AS (

        SELECT
            candidates.*,

            ROW_NUMBER() OVER (

                PARTITION BY
                    location_id,
                    local_date

                ORDER BY
                    prediction_time DESC,
                    created_at DESC NULLS LAST,
                    prediction_id DESC

            ) AS position

        FROM candidates
    )

    SELECT
        location_id,
        local_date,
        risk_level,
        latitude,
        longitude

    FROM ranked

    WHERE position = 1

    ORDER BY
        local_date,
        location_id
""")

#AGENCY HISTORICAL TRENDS ENDPOINT
@router.get("/trends")
def agency_trends(days: int = Query(30, ge=1, le=365), subcounty: str | None = Query(None, max_length=150), ward: str | None = Query(None, max_length=150)):  
    #Return recorded daily risk-classification counts.
    #GET /api/agency/trends?days=30
    #Optional filters: subcounty, ward
    #Each location contributes at most one classification to each recorded Nairobi-local calendar day.
    #Only stored prediction records are used.
    #No missing dates or classifications are estimated.
  
    #RESOLVE ADMINISTRATIVE FILTERS
    selections = (selected_boundaries("subcounty", subcounty), selected_boundaries("ward", ward))

    #DETERMINE NAIROBI-LOCAL DATE RANGE
    # Include today and the previous (days - 1)
    # Nairobi-local calendar dates.
    today = datetime.now(NAIROBI_TZ).date()
    first_date = (today - timedelta(days=days - 1))
    end_date = (today + timedelta(days=1))
    first_local = datetime.combine(first_date, time.min, tzinfo=NAIROBI_TZ)
    end_local = datetime.combine(end_date, time.min, tzinfo=NAIROBI_TZ)

    #CONVERT BOUNDARIES TO UTC
    # PostgreSQL prediction_time is TIMESTAMPTZ.
    # The operational prediction pipeline produces timezone-aware UTC timestamps.
    # Keep these boundaries timezone-aware.
    # Do NOT remove tzinfo or interpret timestamps as local naive datetime values.

    start_time = first_local.astimezone(timezone.utc)
    end_time = end_local.astimezone(timezone.utc)

    #RETRIEVE STORED PREDICTIONS
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(DAILY_PREDICTIONS_SQL, {"start_time": start_time, "end_time": end_time}).mappings().all()

    except SQLAlchemyError as exc:
        logger.exception("Agency historical trends query failed")
        raise HTTPException(status_code=503, detail=("Historical monitoring data is unavailable.")) from exc

    #INITIALIZE DAILY RISK COUNTS
    daily_counts = defaultdict(lambda: {"HIGH": 0, "MODERATE": 0, "LOW": 0})

    # Cache geographic membership for each monitor location during the current API request.
    location_matches = {}

    #APPLY GEOGRAPHIC FILTERING
    for row in rows:
        location_id = row["location_id"]
        if location_id not in location_matches:
            # Countywide query: no spatial filtering.
            if all(value is None for value in selections):
                matches = True

            # Monitoring point does not have usable coordinates for geographic filtering.    
            elif (row["latitude"] is None or row["longitude"] is None):
                matches = False
            else:
                matches = point_matches_boundaries(float(row["latitude"]), float(row["longitude"]), selections)

            location_matches[location_id] = matches

        if not location_matches[location_id]:
            continue

        #VALIDATE THE STORED RISK CLASSIFICATION
        risk = str(row["risk_level"] or "").strip().upper()

        if risk not in {"HIGH", "MODERATE", "LOW"}:
            continue

        # Each row represents the latest classification for one location on one recorded calendar day.
        daily_counts[row["local_date"]][risk] += 1

    #BUILD THE FRONTEND RESPONSE
    daily = [
        {
            "local_date": recorded_date.isoformat(),
            **daily_counts[recorded_date],
        }
        for recorded_date in sorted(daily_counts)
    ]

    #RETURN HISTORICAL CLASSIFICATION COUNTS
    return {
        "daily": daily,
        "days": days,
        # Calendar dates used for reporting.
        "timezone": "Africa/Nairobi",
        # Operational prediction timestamps are UTC instants stored in PostgreSQL TIMESTAMPTZ.
        "prediction_timestamp_timezone": "UTC",
        "filters": {"subcounty": subcounty or "ALL", "ward": ward or "ALL"},
        "count_unit": ("latest classification per monitoring point per recorded day")
    }
