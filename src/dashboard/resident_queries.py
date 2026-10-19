from src.dashboard.database import fetch_rows

#daily history of deployed model assessments. The last operational run on each Nairobi calendar date is used so that operational reruns never inflate a daily chart
RISK_HISTORY_SQL = """
WITH daily_predictions AS (
    SELECT
        (p.prediction_time AT TIME ZONE 'Africa/Nairobi')::date AS local_date,
        p.prediction_time,
        p.risk_score::double precision AS risk_score,
        p.risk_level,
        ROW_NUMBER() OVER (
            PARTITION BY (p.prediction_time AT TIME ZONE 'Africa/Nairobi')::date
            ORDER BY p.prediction_time DESC, p.prediction_id DESC
        ) AS row_number
    FROM flood_risk_prediction p
    JOIN model m ON m.model_id = p.model_id
    WHERE p.location_id = :location_id
      AND m.is_active = TRUE
)
SELECT local_date, prediction_time, risk_score, risk_level
FROM daily_predictions
WHERE row_number = 1
ORDER BY local_date DESC
LIMIT :limit
"""

#weather_data currently stores Open-Meteo's current readings, not a complete daily precipitation tptal
#chart the latest reading per date instead of incorrectly summing readings and refering to them as daily rainfall
WEATHER_HISTORY_SQL = """
WITH daily_weather AS (
    SELECT
        (w.timestamp AT TIME ZONE 'Africa/Nairobi')::date AS local_date,
        w.timestamp,
        w.rainfall::double precision AS rainfall,
        w.temperature::double precision AS temperature,
        ROW_NUMBER() OVER (
            PARTITION BY (w.timestamp AT TIME ZONE 'Africa/Nairobi')::date
            ORDER BY w.timestamp DESC, w.weather_id DESC
        ) AS row_number
    FROM weather_data w
    JOIN data_source s ON s.source_id = w.source_id
    WHERE w.location_id = :location_id
      AND s.name = 'Open-Meteo Best Match'
)
SELECT local_date, timestamp, rainfall, temperature
FROM daily_weather
WHERE row_number = 1
ORDER BY local_date DESC
LIMIT :limit
"""

def get_resident_history(location_id: int, days: int = 30) -> dict:
    #restrict expensive chart queries and keep outputs in chronological order

    days = max(1, min(days, 90))
    parameters = {"location_id": location_id, "limit": days}
    predictions = fetch_rows(RISK_HISTORY_SQL, parameters)
    weather = fetch_rows(WEATHER_HISTORY_SQL, parameters)

    return{
        "location_id": location_id,
        "predictions": list(reversed(predictions)),
        "weather_readings": list(reversed(weather)),
        "note": "Precipitation values are stored weather snapshots, not daily totals.",
    }