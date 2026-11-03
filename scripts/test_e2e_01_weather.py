import argparse
import math
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd

from src.acquisition.open_meteo import OpenMeteoClient
from src.operational.persistence import (create_database_engine, load_operational_locations)
from src.operational.runtime_features import (build_weather_features)

TIMEZONE = "Africa/Nairobi"
BATCH_SIZE = 40

RAINFALL_WINDOWS = {
    "rain_1d": 24,
    "rain_3d": 72,
    "rain_7d": 168,
}

ECMWF_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "precipitation",
    "soil_moisture_0_to_7cm",
    "soil_moisture_7_to_28cm",
)

def log(message):
    print(message, flush=True)

def require(condition, description):
    if not condition:
        raise AssertionError(description)
    log(f"[PASS] {description}")

def normalize_local_times(values):
    #Interpret naive local_time values as Nairobi time.
    #Convert timezone-aware values to Nairobi time.
    #Return timezone-naive Nairobi-local timestamps for comparison against local calendar-day boundaries.
    result = pd.to_datetime(values, errors="coerce")
    require(result.notna().all(), "Hourly timestamps are parseable")

    if result.dt.tz is None:
        result = result.dt.tz_localize(TIMEZONE)
    else:
        result = result.dt.tz_convert(TIMEZONE)

    return result.dt.tz_localize(None)

def prepare_hourly(data, name, required_columns):
    require(isinstance(data, pd.DataFrame), f"{name} returned a DataFrame")

    missing = set(required_columns) - set(data.columns)

    require(not missing, f"{name} required columns are present: " f"{sorted(missing)}")

    result = data.copy()
    result["location_id"] = (result["location_id"].astype(str))
    result["local_time"] = normalize_local_times(result["local_time"])
    duplicate_count = result.duplicated(["location_id", "local_time"]).sum()

    require(duplicate_count == 0, f"{name} contains no duplicate location-hour records",)

    return result

def get_window(frame, location_id, end, hours):
    start = end - timedelta(hours=hours)
    return frame.loc[(frame["location_id"] == str(location_id)) & (frame["local_time"] >= start) & (frame["local_time"] < end)].copy()

def validate_numeric_window(frame, location_id, end, hours, columns, label):
    window = get_window(frame, location_id, end, hours)

    require(len(window) == hours, f"{label}: exactly {hours} hourly records")

    timestamps = set(window["local_time"])
    expected = {end - timedelta(hours=offset) for offset in range(1, hours + 1)}

    require(timestamps == expected, f"{label}: complete continuous hourly coverage")

    for column in columns:
        numbers = pd.to_numeric(window[column], errors="coerce")
        require(numbers.notna().all() and numbers.map(math.isfinite).all(), f"{label}: {column} contains valid numeric values")
    return window

def select_locations(all_locations, test_all):
    if test_all:
        return all_locations.copy()

    count = len(all_locations)
    indices = sorted({0, count // 2, count - 1})

    return all_locations.iloc[indices].copy()

def main():
    parser = argparse.ArgumentParser(description="FloodWatch E2E-01 weather integration test")
    parser.add_argument("--all", action="store_true", help="Test all operational monitoring locations.")
    args = parser.parse_args()
    started = time.perf_counter()
    prediction_date = datetime.now(ZoneInfo(TIMEZONE)).date()
    prediction_midnight = pd.Timestamp(prediction_date)

    log("\n" + "=" * 50)
    log("NAIROBI FLOODWATCH — E2E-01")
    log("=" * 50)
    log(f"Prediction date: {prediction_date}")
    log(f"Timezone: {TIMEZONE}")
    log("Mode: ALL LOCATIONS" if args.all else "Mode: THREE-LOCATION SMOKE TEST")

    #PostgreSQL monitoring locations
    log("\n--- DATABASE LOCATIONS ---")
    engine = create_database_engine()
    all_locations = load_operational_locations(engine)

    require(isinstance(all_locations, pd.DataFrame) and not all_locations.empty, "PostgreSQL returned operational monitoring locations")

    locations = select_locations(all_locations, args.all)
    location_ids = (locations["location_id"].astype(str).tolist())

    require(len(set(location_ids)) == len(location_ids), "Selected monitoring-location IDs are unique")
    log(f"Available locations: {len(all_locations)}")
    log(f"Locations being tested: {len(locations)}")

    #Live Open-Meteo acquisition
    log("\n--- OPEN-METEO ACQUISITION ---")

    client = OpenMeteoClient()
    weather = client.fetch_locations_batched(locations, batch_size=BATCH_SIZE)
    required_sources = {"generic_hourly", "ecmwf_hourly", "current"}
    require(isinstance(weather, dict) and required_sources.issubset(weather), "Both hourly sources and current readings returned")

    generic = prepare_hourly(weather["generic_hourly"], "Open-Meteo Best Match", ["location_id", "local_time", "precipitation"])
    ecmwf = prepare_hourly(weather["ecmwf_hourly"], "ECMWF IFS", ["location_id", "local_time", *ECMWF_VARIABLES])
    current = weather["current"]
    require(isinstance(current, pd.DataFrame) and len(current) == len(locations), "Current weather contains one row per location")

    log(f"Best Match hourly rows: {len(generic)}")
    log(f"ECMWF hourly rows: {len(ecmwf)}")
    log(f"Current weather rows: {len(current)}")

    expected_ids = set(location_ids)
    require(set(generic["location_id"]) == expected_ids, "Best Match covers every selected location")

    require(set(ecmwf["location_id"]) == expected_ids, "ECMWF covers every selected location")

    #Antecedent window completeness
    log("\n--- WEATHER WINDOWS ---")
    independently_calculated = {}

    for location_id in location_ids:
        log(f"\nLocation: {location_id}")
        values = {}

        for name, hours in RAINFALL_WINDOWS.items():
            window = validate_numeric_window(generic, location_id, prediction_midnight, hours, ["precipitation"], f"{location_id} {name}")
            rainfall = pd.to_numeric(window["precipitation"])
            require((rainfall >= 0).all(), f"{location_id} {name}: rainfall is nonnegative")
            values[name] = float(rainfall.sum())

        validate_numeric_window(ecmwf, location_id, prediction_midnight, 24, list(ECMWF_VARIABLES), f"{location_id} ECMWF previous day")
        require(values["rain_1d"] <= values["rain_3d"] + 1e-8 <= values["rain_7d"] + 2e-8, f"{location_id}: rainfall accumulations are consistent")

        independently_calculated[location_id] = values
        log("Independent rainfall sums (mm): " + ", ".join(f"{key}={value:.2f}" for key, value in values.items()))

    #Actual operational feature engineering
    log("\n--- FEATURE GENERATION ---")
    features = build_weather_features(weather["generic_hourly"], weather["ecmwf_hourly"], prediction_date=prediction_date)
    require(isinstance(features, pd.DataFrame), "Feature builder returned a DataFrame")
    require(len(features) == len(locations), "One engineered feature row per monitoring location")

    missing_columns = ({"location_id", *RAINFALL_WINDOWS.keys()} - set(features.columns))
    require(not missing_columns, f"Required rainfall feature columns exist: " f"{sorted(missing_columns)}")

    feature_ids = features["location_id"].astype(str)
    require(set(feature_ids) == expected_ids and not feature_ids.duplicated().any(), "Feature records match selected monitoring locations")

    for location_id in location_ids:
        row = features.loc[feature_ids == location_id].iloc[0]
        expected = independently_calculated[location_id]

        for feature, calculated in expected.items():
            value = float(row[feature])
            require(math.isfinite(value) and math.isclose(value, calculated, rel_tol=0, abs_tol=0.05), f"{location_id} {feature}: " "engineered value matches independent sum")

    # FINAL RESULT
    elapsed = time.perf_counter() - started

    log("\n" + "=" * 50)
    log("E2E-01 RESULT: PASS")
    log("=" * 50)
    log(f"Monitoring locations validated: {len(locations)}")
    log(f"Feature records generated: {len(features)}")
    log(f"Execution duration: {elapsed:.2f} seconds")
    log("No weather records, predictions or alerts were written to PostgreSQL.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nE2E-01 RESULT: FAIL\n" f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
