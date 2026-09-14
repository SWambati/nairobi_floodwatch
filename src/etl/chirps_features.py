from datetime import timedelta
import pandas as pd
from config.paths import (FEATURES_DIR, PROCESSED_DIR,)
from src.etl.chirps import (EXPECTED_END_DATE, EXPECTED_START_DATE, extract_daily_rainfall, load_groundsource_event_requests,)

#configuration
RAINFALL_WINDOWS = (1, 3, 7, )
MAX_LOOKBACK_DAYS = max(RAINFALL_WINDOWS)
CHIRPS_PROCESSED_DIR = (PROCESSED_DIR/"weather"/"chirps")
CHIRPS_FEATURES_DIR = (FEATURES_DIR/"weather"/"chirps")

#preceeding request generation

def build_antecedent_rainfall_requests(event_requests: pd.DataFrame) -> pd.DataFrame:

    #Expand each flood-event request into 7 daily CHIRPS requests representing.
    #the flood event is excluded.
    #for event occurring in date D eg. days_before_target = 1, D - 1. This prevents using complete event-day rainfall total when precise flood time is not known

    dataframe = event_requests.copy()

    required_columns = {
        "label_record_id",
        "event_group_id",
        "date",
        "latitude",
        "longitude",
        "verification_status",
    }
    missing_columns = required_columns - set(dataframe.columns)

    if missing_columns:
        raise ValueError("Rainfall feature requests are missing "f"columns: {sorted(missing_columns)}")

    if dataframe["label_record_id"].duplicated().any():
        raise ValueError("Duplicated label_record_id values were found in rainfall feature requests")

    dataframe["target_date"] = (pd.to_datetime(dataframe["date"], errors="raise").dt.date)

    #generate dates extracted from the base dataframe event-date column

    dataframe = dataframe.drop(columns=["date"])

    #build offsets 1 to 7

    offsets = pd.DataFrame({
        "days_before_target": range(1, MAX_LOOKBACK_DAYS + 1)
    })
    dataframe["__join_key"] = 1
    offsets["__join_key"] = 1

    expanded = dataframe.merge(offsets, on="__join_key", how="inner")
    expanded = expanded.drop(columns=["__join_key"])
    expanded["date"] = expanded.apply(lambda row: row["target_date"] - timedelta(days=int(row["days_before_target"])), axis=1)

    #validate CHIRPS temporal coverage

    minimum_date = expanded["date"].min()
    maximum_date = expanded["date"].max()

    if minimum_date < EXPECTED_START_DATE:
        raise ValueError("Antecedent rainfall requests extends before available CHIRPS coverage: "f"{minimum_date}")
    if maximum_date > EXPECTED_END_DATE:
        raise ValueError("Antecedent rainfall requests extends beyond available CHIRPS coverage: "f"{maximum_date}")

    return(expanded.sort_values(["label_record_id", "days_before_target"]).reset_index(drop=True))

#feature calculation
def calculate_rainfall_features(daily_rainfall: pd.DataFrame) -> pd.DataFrame:

    #Convert daily antecedent rainfall observations into cummulative rainfall features expressed in mm
    #Eg features: rain_1d = rainfall during D-1, rain_3d = sum of rainfall from D-3 to -1, rain_7d = sum of rainfall from D-7 to D-1

    dataframe = daily_rainfall.copy()

    required_columns = {
        "label_record_id",
        "event_group_id",
        "target_date",
        "date",
        "days_before_target",
        "latitude",
        "longitude",
        "verification_status",
        "rainfall_mm",
        "chirps_sampling_method",
        "chirps_fallback_distance_pixels",
    }
    missing_columns = (required_columns - set(dataframe.columns))

    if missing_columns:
        raise ValueError("Daily rainfall data is missing columns: "f"{sorted(missing_columns)}")

    #missing rainfall values not allowed in feature stage

    missing_rainfall = dataframe["rainfall_mm"].isna()

    if missing_rainfall.any():
        raise ValueError("Cannot construct CHIRPS rainfall "f"features:{missing_rainfall.sum()} daily observations are missing")

    #validate exactly 7 antecedent days per event

    expected_offsets = set(range(1, MAX_LOOKBACK_DAYS + 1))

    for (label_record_id, group) in dataframe.groupby("label_record_id"):
        actual_offsets = set(group["days_before_target"].astype(int))

        if actual_offsets != expected_offsets:
            raise ValueError("Incomplete antecedent rainfall window "f"for {label_record_id}." f"Expected offsets: {sorted(expected_offsets)}, actual offsets: {sorted(actual_offsets)}")

    #aggregate features
    feature_records = []

    for (label_record_id, group) in dataframe.groupby("label_record_id", sort=False):
        group = group.sort_values("days_before_target")

        first = group.iloc[0]

        rain_1d  = group.loc[group["days_before_target"] <=1, "rainfall_mm"].sum()
        rain_3d  = group.loc[group["days_before_target"] <=3, "rainfall_mm"].sum()
        rain_7d  = group.loc[group["days_before_target"] <=7, "rainfall_mm"].sum()

        fallback_rows = group[group["chirps_sampling_method"] == "nearest_valid"]

        fallback_days_7d = len(fallback_rows)

        if fallback_rows.empty: 
            maximum_fallback_distance = 0
        else:
            maximum_fallback_distance = int(fallback_rows["chirps_fallback_distance_pixels"].max())

        feature_records.append(
            {
                "label_record_id": label_record_id,
                "event_group_id": first["event_group_id"],
                "target_date": first["target_date"],
                "latitude": first["latitude"],
                "longitude": first["longitude"],
                "verification_status": first["verification_status"],
                "rain_1d": rain_1d,
                "rain_3d": rain_3d,
                "rain_7d": rain_7d,
                "chirps_fallback_days_7d": fallback_days_7d,
                "chirps_max_fallback_distance_pixels": maximum_fallback_distance,
            })
        features = pd.DataFrame(feature_records)

        #logical consistency

        invalid_1_to_3 = (features["rain_1d"] > features["rain_3d"])
        invalid_3_to_7 = (features["rain_3d"] > features["rain_7d"])

        if (invalid_1_to_3.any() or invalid_3_to_7.any()):
            raise ValueError("Rainfall accumulation consistency check failed. Expected rain_1d <= rain_3d <= rain_7d")

        if features["label_record_id"].duplicated().any():
            raise ValueError("Duplicated rainfall feature rows were generated")

        return features.reset_index(drop=True)

#pipeline
def process_chirps_features() -> tuple[pd.DataFrame, pd.DataFrame]:

    #run the complete CHIRPS rainfall-feature stage to return daily_rainfall and rainfall_features

    event_requests = (load_groundsource_event_requests())

    expanded_requests = (build_antecedent_rainfall_requests(event_requests))

    daily_rainfall =(extract_daily_rainfall(expanded_requests))

    rainfall_features = (calculate_rainfall_features(daily_rainfall))

    return (daily_rainfall, rainfall_features)

#output writer
def save_chirps_feature_output(daily_rainfall:pd.DataFrame, rainfall_features:pd.DataFrame,) -> None:

    #save both expanded daily rainfall observations and final cumulative CHIRPS features

    CHIRPS_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CHIRPS_FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    daily_output =(CHIRPS_PROCESSED_DIR / "groundsource_antecedent_daily_rainfall.csv")
    feature_output =(CHIRPS_FEATURE_DIR / "groundsource_rainfall_features.csv")

    daily_rainfall.to_csv(daily_output, index=False)
    rainfall_features.to_csv(feature_output, index=False)

    print(f"Saved antecedent daily rainfall: {daily_output}")
    print(f"Saved CHIRPS rainfall features: {feature_output}")
