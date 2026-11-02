from src.dashboard.database import fetch_rows

RISK_HISTORY_SQL = """
WITH daily_predictions AS (
    SELECT
        (p.prediction_time AT TIME ZONE 'Africa/Nairobi')::date
            AS local_date,

        p.prediction_time,

        p.risk_score::double precision AS risk_score,
        p.risk_level,

        -- Stored operational rainfall accumulations
        NULLIF(
            p.prediction_data::jsonb
                -> 'predictors' ->> 'rain_1d',
            ''
        )::double precision AS rain_1d,

        NULLIF(
            p.prediction_data::jsonb
                -> 'predictors' ->> 'rain_3d',
            ''
        )::double precision AS rain_3d,

        NULLIF(
            p.prediction_data::jsonb
                -> 'predictors' ->> 'rain_7d',
            ''
        )::double precision AS rain_7d,

        ROW_NUMBER() OVER (
            PARTITION BY (
                p.prediction_time
                    AT TIME ZONE 'Africa/Nairobi'
            )::date

            ORDER BY
                p.prediction_time DESC,
                p.prediction_id DESC
        ) AS row_number

    FROM flood_risk_prediction p

    JOIN model m
        ON m.model_id = p.model_id

    WHERE p.location_id = :location_id
      AND m.is_active = TRUE
)

SELECT
    local_date,
    prediction_time,
    risk_score,
    risk_level,
    rain_1d,
    rain_3d,
    rain_7d

FROM daily_predictions

WHERE row_number = 1

ORDER BY local_date DESC

LIMIT :limit
"""

#HISTORICAL WEATHER SNAPSHOTS
# Preserve the existing weather history response for compatibility with current dashboard code.
# These are individual weather readings, not accumulated daily rainfall totals.

WEATHER_HISTORY_SQL = """
WITH daily_weather AS (
    SELECT
        (w.timestamp AT TIME ZONE 'Africa/Nairobi')::date
            AS local_date,

        w.timestamp,

        w.rainfall::double precision AS rainfall,
        w.temperature::double precision AS temperature,

        ROW_NUMBER() OVER (
            PARTITION BY (
                w.timestamp AT TIME ZONE 'Africa/Nairobi'
            )::date

            ORDER BY
                w.timestamp DESC,
                w.weather_id DESC
        ) AS row_number

    FROM weather_data w

    JOIN data_source s
        ON s.source_id = w.source_id

    WHERE w.location_id = :location_id
      AND s.name = 'Open-Meteo Best Match'
)

SELECT
    local_date,
    timestamp,
    rainfall,
    temperature

FROM daily_weather

WHERE row_number = 1

ORDER BY local_date DESC

LIMIT :limit
"""

#RESIDENT HISTORY SERVICE
def get_resident_history(location_id: int, days: int = 30) -> dict:

    # Limit historical assessment records.
    days = max(1, min(days, 90))
    parameters = {"location_id": location_id, "limit": days}
    predictions = fetch_rows(RISK_HISTORY_SQL, parameters)
    weather = fetch_rows(WEATHER_HISTORY_SQL, parameters)

    return {
        "location_id": location_id,

        # Chronological prediction history, including stored rainfall accumulations.
        "predictions": list(reversed(predictions)),

        # Retained for compatibility.
        "weather_readings": list(reversed(weather)),
        "rainfall_source": "Open-Meteo Best Match",
        "rainfall_unit": "mm",
        "rainfall_windows": {"rain_1d": 24, "rain_3d": 72, "rain_7d": 168},
        "note": (
            "Rainfall accumulations represent antecedent precipitation estimates stored with each operational prediction. "
            "Weather readings are separate snapshots, not daily rainfall totals."),
    }
