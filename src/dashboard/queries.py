from src.dashboard.database import fetch_rows


# Latest prediction per observation point, restricted to active models.
OVERVIEW_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (p.location_id)
        p.location_id,
        p.prediction_id,
        p.risk_level,
        p.prediction_time
    FROM flood_risk_prediction p
    JOIN model m ON m.model_id = p.model_id
    WHERE m.is_active = TRUE
    ORDER BY p.location_id, p.prediction_time DESC, p.prediction_id DESC
)
SELECT
    COUNT(*)::integer AS monitored_locations,
    COUNT(p.prediction_id)::integer AS assessed_locations,
    COUNT(*) FILTER (WHERE p.risk_level = 'LOW')::integer AS low,
    COUNT(*) FILTER (WHERE p.risk_level = 'MODERATE')::integer AS moderate,
    COUNT(*) FILTER (WHERE p.risk_level = 'HIGH')::integer AS high,
    MAX(p.prediction_time) AS latest_prediction,
    (
        SELECT COUNT(*)::integer
        FROM flood_alert a
        JOIN latest lp ON lp.prediction_id = a.prediction_id
        WHERE a.status = 'ACTIVE'
          AND a.expires_at > NOW()
    ) AS active_advisories
FROM location l
LEFT JOIN latest p ON p.location_id = l.location_id
WHERE l.administrative_level = 'observation_point'
"""


# LATERAL joins select the latest relevant record for each location.
# This avoids multiplying location rows when historical observations exist.
LOCATIONS_SQL = """
SELECT
    l.location_id,
    l.name,
    l.administrative_level,
    l.latitude::double precision AS latitude,
    l.longitude::double precision AS longitude,

    p.prediction_id,
    p.risk_level,
    p.risk_score,
    p.prediction_time,

    w.timestamp AS weather_timestamp,
    w.temperature,
    w.humidity,
    w.rainfall,
    w.pressure,

    h.flood_hazard_score,

    a.alert_level,
    a.title AS alert_title,
    a.message AS alert_message,
    a.instructions AS alert_instructions,
    a.expires_at AS alert_expires_at

FROM location l

LEFT JOIN LATERAL (
    SELECT
        p.prediction_id,
        p.risk_level,
        p.risk_score::double precision AS risk_score,
        p.prediction_time
    FROM flood_risk_prediction p
    JOIN model m ON m.model_id = p.model_id
    WHERE p.location_id = l.location_id
      AND m.is_active = TRUE
    ORDER BY p.prediction_time DESC, p.prediction_id DESC
    LIMIT 1
) p ON TRUE

LEFT JOIN LATERAL (
    SELECT
        w.timestamp,
        w.temperature::double precision AS temperature,
        w.humidity::double precision AS humidity,
        w.rainfall::double precision AS rainfall,
        w.pressure::double precision AS pressure
    FROM weather_data w
    JOIN data_source s ON s.source_id = w.source_id
    WHERE w.location_id = l.location_id
      AND s.name = 'Open-Meteo Best Match'
    ORDER BY w.timestamp DESC, w.weather_id DESC
    LIMIT 1
) w ON TRUE

LEFT JOIN LATERAL (
    SELECT
        f.feature_value::double precision AS flood_hazard_score
    FROM environmental_feature f
    WHERE f.location_id = l.location_id
      AND f.feature_name = 'flood_hazard_score'
    ORDER BY f.effective_date DESC NULLS LAST, f.feature_id DESC
    LIMIT 1
) h ON TRUE

LEFT JOIN LATERAL (
    SELECT
        a.alert_level,
        a.title,
        a.message,
        a.instructions,
        a.expires_at
    FROM flood_alert a
    WHERE a.prediction_id = p.prediction_id
      AND a.status = 'ACTIVE'
      AND a.expires_at > NOW()
    ORDER BY a.issued_at DESC, a.alert_id DESC
    LIMIT 1
) a ON TRUE

WHERE l.administrative_level = 'observation_point'
"""


def get_overview() -> dict:
    # Return a single citywide summary from PostgreSQL.

    rows = fetch_rows(OVERVIEW_SQL)

    if not rows:
        raise RuntimeError("Unable to retrieve FloodWatch monitoring statistics.")

    return rows[0]


def get_locations(location_id: int | None = None) -> list[dict]:
    # Add an optional location filter using a bound SQL parameter.
    # All returned location rows remain distinct observation points.

    parameters = {}
    location_filter = ""

    if location_id is not None:
        location_filter = " AND l.location_id = :location_id"
        parameters["location_id"] = location_id

    query = (LOCATIONS_SQL + location_filter + " ORDER BY l.name, l.location_id")

    return fetch_rows(query, parameters)
