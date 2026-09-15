from datetime import timedelta

import numpy as np
import pandas as pd

from config.paths import (
    FEATURES_DIR,
    PROCESSED_DIR,
)


#configuration
ERA5_PROCESSED_DIR = (PROCESSED_DIR/"weather"/"era5_land")
ERA5_FEATURE_DIR = (FEATURES_DIR/"weather"/"era5_land")

ERA5_HOURLY_FILE = (ERA5_PROCESSED_DIR/"era5_land_hourly_canonical.parquet")
ERA5_MAPPING_FILE = (ERA5_PROCESSED_DIR/"groundsource_era5_grid_mapping.csv")


#load data
def load_era5_feature_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:

    if not ERA5_HOURLY_FILE.exists():
        raise FileNotFoundError(f"Canonical ERA5-Land archive not found: {ERA5_HOURLY_FILE}")

    if not ERA5_MAPPING_FILE.exists():
        raise FileNotFoundError(f"Groundsource ERA5 mapping not found: {ERA5_MAPPING_FILE}")

    era5_data = pd.read_parquet(ERA5_HOURLY_FILE)
    mapping = pd.read_csv(ERA5_MAPPING_FILE)

    return (era5_data, mapping)


#unit conversion and meteorological derivation
def derive_hourly_meteorology(era5_data: pd.DataFrame) -> pd.DataFrame:

    dataframe = era5_data.copy()

    required_columns = {
        "timestamp",
        "latitude",
        "longitude",
        "t2m",
        "d2m",
        "sp",
        "tp",
        "swvl1",
        "swvl2",
    }

    missing_columns = required_columns - set(dataframe.columns)

    if missing_columns:
        raise ValueError(f"ERA5-Land archive is missing columns: {sorted(missing_columns)}")

    dataframe["timestamp"] = pd.to_datetime(
        dataframe["timestamp"],
        errors="raise",
        utc=True
    )

    #Kelvin to Celsius
    dataframe["temperature_c"] = dataframe["t2m"] - 273.15
    dataframe["dewpoint_c"] = dataframe["d2m"] - 273.15

    #relative humidity from air temperature and dew point
    temperature_term = (
        17.625 * dataframe["temperature_c"]
        / (243.04 + dataframe["temperature_c"])
    )

    dewpoint_term = (
        17.625 * dataframe["dewpoint_c"]
        / (243.04 + dataframe["dewpoint_c"])
    )

    dataframe["relative_humidity_pct"] = (
        100
        * np.exp(dewpoint_term)
        / np.exp(temperature_term)
    ).clip(0, 100)

    #Pa to hPa
    dataframe["pressure_hpa"] = dataframe["sp"] / 100

    #metres to millimetres
    dataframe["precipitation_mm"] = dataframe["tp"] * 1000

    negative_precipitation = (
        dataframe["precipitation_mm"] < 0
    )

    if negative_precipitation.any():

        minimum_precipitation = (
            dataframe.loc[
                negative_precipitation,
                "precipitation_mm"
            ].min()
        )

        if minimum_precipitation < -0.01:
            raise ValueError(
                "Materially negative ERA5-Land precipitation "
                f"value detected: {minimum_precipitation:.6f} mm"
            )

        dataframe.loc[
            negative_precipitation,
            "precipitation_mm"
        ] = 0.0

    #Convert UTC timestamps to Nairobi local time so the
    #antecedent feature day corresponds to the flood-label date.
    dataframe["nairobi_timestamp"] = (
        dataframe["timestamp"]
        .dt.tz_convert("Africa/Nairobi")
    )

    dataframe["nairobi_date"] = (
        dataframe["nairobi_timestamp"]
        .dt.date
    )

    return dataframe


#build feature requests
def build_era5_feature_requests(mapping: pd.DataFrame) -> pd.DataFrame:

    dataframe = mapping.copy()

    required_columns = {
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status",
        "era5_latitude",
        "era5_longitude",
        "era5_distance_km",
    }

    missing_columns = required_columns - set(dataframe.columns)

    if missing_columns:
        raise ValueError(f"ERA5 feature mapping is missing columns: {sorted(missing_columns)}")

    if dataframe["label_record_id"].duplicated().any():
        raise ValueError("Duplicate label_record_id values found in ERA5 mapping")

    dataframe["target_date"] = (
        pd.to_datetime(
            dataframe["start_date"],
            errors="raise"
        ).dt.date
    )

    dataframe["feature_date"] = (
        dataframe["target_date"]
        .map(lambda value: value - timedelta(days=1))
    )

    return dataframe


#extract antecedent hourly observations
def extract_antecedent_era5_hours(
    era5_data: pd.DataFrame,
    requests: pd.DataFrame,
) -> pd.DataFrame:

    hourly = era5_data.copy()
    request_data = requests.copy()

    hourly = hourly.rename(
        columns={
            "latitude": "era5_latitude",
            "longitude": "era5_longitude",
            "nairobi_date": "feature_date",
        }
    )

    hourly_columns = [
        "timestamp",
        "nairobi_timestamp",
        "feature_date",
        "era5_latitude",
        "era5_longitude",
        "temperature_c",
        "dewpoint_c",
        "relative_humidity_pct",
        "pressure_hpa",
        "precipitation_mm",
        "swvl1",
        "swvl2",
    ]

    hourly = hourly[hourly_columns]

    antecedent = request_data.merge(
        hourly,
        on=[
            "era5_latitude",
            "era5_longitude",
            "feature_date",
        ],
        how="left",
        validate="one_to_many",
    )

    hourly_counts = (
        antecedent
        .groupby("label_record_id")
        .size()
    )

    invalid_counts = hourly_counts[
        hourly_counts != 24
    ]

    if not invalid_counts.empty:
        raise ValueError(
            "One or more flood observations do not have "
            f"exactly 24 antecedent ERA5 hours:\n{invalid_counts}"
        )

    meteorological_columns = [
        "temperature_c",
        "relative_humidity_pct",
        "pressure_hpa",
        "precipitation_mm",
        "swvl1",
        "swvl2",
    ]

    if antecedent[meteorological_columns].isna().any().any():
        raise ValueError(
            "Missing ERA5 meteorological values detected "
            "after antecedent-hour extraction"
        )

    return antecedent.reset_index(drop=True)


#aggregate features
def calculate_era5_features(
    antecedent_hourly: pd.DataFrame,
) -> pd.DataFrame:

    feature_records = []

    for (label_record_id, group) in antecedent_hourly.groupby(
        "label_record_id",
        sort=False
    ):

        first = group.iloc[0]

        feature_records.append(
            {
                "label_record_id": label_record_id,
                "event_group_id": first["event_group_id"],
                "target_date": first["target_date"],
                "feature_date": first["feature_date"],
                "latitude": first["latitude"],
                "longitude": first["longitude"],
                "verification_status": first["verification_status"],
                "era5_latitude": first["era5_latitude"],
                "era5_longitude": first["era5_longitude"],
                "era5_distance_km": first["era5_distance_km"],

                "temperature_mean_c": float(group["temperature_c"].mean()),
                "temperature_min_c": float(group["temperature_c"].min()),
                "temperature_max_c": float(group["temperature_c"].max()),

                "relative_humidity_mean_pct": float(
                    group["relative_humidity_pct"].mean()
                ),

                "pressure_mean_hpa": float(
                    group["pressure_hpa"].mean()
                ),

                "era5_precip_1d_mm": float(
                    group["precipitation_mm"].sum()
                ),

                "soil_moisture_layer1_mean": float(
                    group["swvl1"].mean()
                ),

                "soil_moisture_layer2_mean": float(
                    group["swvl2"].mean()
                ),
            }
        )

    features = pd.DataFrame(feature_records)

    expected_rows = antecedent_hourly[
        "label_record_id"
    ].nunique()

    if len(features) != expected_rows:
        raise ValueError(
            "ERA5 feature row count mismatch "
            f"(expected {expected_rows}, got {len(features)})"
        )

    if features["label_record_id"].duplicated().any():
        raise ValueError("Duplicate ERA5 meteorological feature rows generated")

    invalid_temperature = (
        features["temperature_min_c"]
        > features["temperature_mean_c"]
    ) | (
        features["temperature_mean_c"]
        > features["temperature_max_c"]
    )

    if invalid_temperature.any():
        raise ValueError(
            "Temperature consistency check failed. Expected "
            "minimum <= mean <= maximum"
        )

    if not features[
        "relative_humidity_mean_pct"
    ].between(0, 100).all():
        raise ValueError("Invalid relative humidity values detected")

    if (features["era5_precip_1d_mm"] < 0).any():
        raise ValueError("Invalid negative ERA5 precipitation totals detected")

    return features.reset_index(drop=True)


#pipeline
def process_era5_features() -> tuple[pd.DataFrame, pd.DataFrame]:

    era5_data, mapping = load_era5_feature_inputs()

    hourly_meteorology = derive_hourly_meteorology(
        era5_data
    )

    requests = build_era5_feature_requests(
        mapping
    )

    antecedent_hourly = extract_antecedent_era5_hours(
        hourly_meteorology,
        requests
    )

    features = calculate_era5_features(
        antecedent_hourly
    )

    return (
        antecedent_hourly,
        features
    )


#output writer
def save_era5_feature_outputs(
    antecedent_hourly: pd.DataFrame,
    features: pd.DataFrame,
) -> None:

    ERA5_PROCESSED_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    ERA5_FEATURE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    hourly_output = (
        ERA5_PROCESSED_DIR
        / "groundsource_era5_antecedent_hourly.parquet"
    )

    feature_output = (
        ERA5_FEATURE_DIR
        / "groundsource_meteorological_features.csv"
    )

    antecedent_hourly.to_parquet(
        hourly_output,
        index=False
    )

    features.to_csv(
        feature_output,
        index=False
    )

    print(
        f"Saved ERA5 antecedent hourly data: "
        f"{hourly_output}"
    )

    print(
        f"Saved ERA5 meteorological features: "
        f"{feature_output}"
    )