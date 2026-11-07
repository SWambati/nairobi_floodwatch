from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from src.model.model_dataset import PREDICTOR_COLUMNS


# Configuration
NAIROBI_TIMEZONE = "Africa/Nairobi"

STATIC_FEATURE_COLUMNS = ["elevation_m", "slope_deg", "flood_hazard_score", "ghsl_built_up_fraction"]
WEATHER_FEATURE_COLUMNS = ["rain_1d", "rain_3d", "rain_7d", "temperature_mean_c", "temperature_min_c", "temperature_max_c", "relative_humidity_mean_pct", "pressure_mean_hpa", "era5_precip_1d_mm", "soil_moisture_layer1_mean", "soil_moisture_layer2_mean"]


def _prepare_hourly(dataframe: pd.DataFrame, required_columns: list[str], dataset_name: str) -> pd.DataFrame:
    missing = set(required_columns) - set(dataframe.columns)

    if missing:
        raise ValueError(f"{dataset_name} is missing columns: {sorted(missing)}")

    frame = dataframe.copy()
    frame["local_time"] = pd.to_datetime(frame["local_time"], errors="raise")
    
    # Reject missing location identifiers or timestamps.
    if frame[["location_id", "local_time"]].isna().any().any():
        raise ValueError(f"{dataset_name} contains missing location IDs or timestamps.")
    
    # Each location must have unique hourly timestamps.
    if frame.duplicated(subset=["location_id", "local_time"]).any():
        raise ValueError(f"{dataset_name} contains duplicate hourly timestamps.")
    
    frame["local_date"] = frame["local_time"].dt.date

    for column in required_columns:
        if column in {"location_id", "local_time"}:
            continue

        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    return frame


def _prediction_date(prediction_date: date | None) -> date:

    if prediction_date is not None:
        return prediction_date

    return datetime.now(ZoneInfo(NAIROBI_TIMEZONE)).date()


def _date_window(dataframe: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    return dataframe[(dataframe["local_date"] >= start_date) & (dataframe["local_date"] <= end_date)].copy()


def _validate_hour_count(dataframe: pd.DataFrame, expected_hours: int, label: str) -> None:

    if len(dataframe) != expected_hours:
        raise ValueError(f"{label} requires {expected_hours} hourly observations; found {len(dataframe)}.")


def build_weather_features(generic_hourly: pd.DataFrame, ecmwf_hourly: pd.DataFrame, prediction_date: date | None = None) -> pd.DataFrame:
    prediction_date = _prediction_date(prediction_date)

    generic = _prepare_hourly(generic_hourly, ["location_id", "local_time", "precipitation"], "Generic Open-Meteo hourly data")
    ecmwf = _prepare_hourly(ecmwf_hourly, ["location_id", "local_time", "temperature_2m", "relative_humidity_2m", "surface_pressure", "precipitation", "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm"], "ECMWF Open-Meteo hourly data")
    generic_locations = set(generic["location_id"].astype(str))
    ecmwf_locations = set(ecmwf["location_id"].astype(str))

    if generic_locations != ecmwf_locations:
        raise ValueError("Generic and ECMWF location sets do not match.")

    previous_day = prediction_date - timedelta(days=1)
    three_day_start = prediction_date - timedelta(days=3)
    seven_day_start = prediction_date - timedelta(days=7)

    rows = []

    for location_id in sorted(generic_locations):
        generic_location = generic[generic["location_id"].astype(str) == location_id]
        ecmwf_location = ecmwf[ecmwf["location_id"].astype(str) == location_id]

        rain_7d_window = _date_window(generic_location, seven_day_start, previous_day)
        rain_3d_window = _date_window(generic_location, three_day_start, previous_day)
        rain_1d_window = _date_window(generic_location, previous_day, previous_day)

        ecmwf_previous_day = _date_window(ecmwf_location, previous_day, previous_day)

        _validate_hour_count(
            rain_7d_window,
            168,
            f"{location_id} seven-day rainfall window",
        )

        _validate_hour_count(
            rain_3d_window,
            72,
            f"{location_id} three-day rainfall window",
        )

        _validate_hour_count(
            rain_1d_window,
            24,
            f"{location_id} one-day rainfall window",
        )

        _validate_hour_count(
            ecmwf_previous_day,
            24,
            f"{location_id} ECMWF previous-day window",
        )

        required_generic = ["precipitation"]

        required_ecmwf = [
            "temperature_2m",
            "relative_humidity_2m",
            "surface_pressure",
            "precipitation",
            "soil_moisture_0_to_7cm",
            "soil_moisture_7_to_28cm",
        ]

        if rain_7d_window[required_generic].isna().any().any():
            raise ValueError(f"{location_id} rainfall window contains missing values.")

        if ecmwf_previous_day[required_ecmwf].isna().any().any():
            raise ValueError(f"{location_id} ECMWF window contains missing values.")

        rows.append({
            "location_id": location_id,
            "prediction_date": prediction_date.isoformat(),
            "rain_1d": rain_1d_window["precipitation"].sum(),
            "rain_3d": rain_3d_window["precipitation"].sum(),
            "rain_7d": rain_7d_window["precipitation"].sum(),
            "temperature_mean_c": ecmwf_previous_day["temperature_2m"].mean(),
            "temperature_min_c": ecmwf_previous_day["temperature_2m"].min(),
            "temperature_max_c": ecmwf_previous_day["temperature_2m"].max(),
            "relative_humidity_mean_pct": ecmwf_previous_day["relative_humidity_2m"].mean(),
            "pressure_mean_hpa": ecmwf_previous_day["surface_pressure"].mean(),
            "era5_precip_1d_mm": ecmwf_previous_day["precipitation"].sum(),
            "soil_moisture_layer1_mean": ecmwf_previous_day["soil_moisture_0_to_7cm"].mean(),
            "soil_moisture_layer2_mean": ecmwf_previous_day["soil_moisture_7_to_28cm"].mean(),
        })

    features = pd.DataFrame(rows)
    values = features[WEATHER_FEATURE_COLUMNS].to_numpy(dtype=float)

    if not np.isfinite(values).all():
        raise ValueError("Generated weather features contain non-finite values.")

    return features


def assemble_model_features(weather_features: pd.DataFrame, static_features: pd.DataFrame) -> pd.DataFrame:

    required_static = {"location_id", *STATIC_FEATURE_COLUMNS}
    missing = required_static - set(static_features.columns)

    if missing:
        raise ValueError(f"Static feature input is missing columns: {sorted(missing)}")

    if static_features["location_id"].duplicated().any():
        raise ValueError("Static feature input contains duplicate location IDs.")

    combined = weather_features.merge(static_features[["location_id", *STATIC_FEATURE_COLUMNS]], on="location_id", how="left", validate="one_to_one")

    if combined[STATIC_FEATURE_COLUMNS].isna().any().any():
        raise ValueError("One or more locations are missing static environmental features.")

    if list(PREDICTOR_COLUMNS) != (WEATHER_FEATURE_COLUMNS + STATIC_FEATURE_COLUMNS):
        raise ValueError("Operational feature definition no longer matches the frozen model predictor schema.")

    values = combined[PREDICTOR_COLUMNS].to_numpy(dtype=float)

    if not np.isfinite(values).all():
        raise ValueError("Operational model features contain non-finite values.")

    return combined[["location_id", "prediction_date", *PREDICTOR_COLUMNS]]