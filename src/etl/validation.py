from pathlib import Path
import numpy as np
import pandas as pd
from config.paths import (FEATURES_DIR, PROCESSED_DIR)


#configuration
VALIDATION_OUTPUT_DIR = (PROCESSED_DIR/"validation")
LABEL_FILE = (PROCESSED_DIR/ "flood_labels"/ "groundsource_positive_candidates.csv")
MASTER_ANCHOR_FILE = (PROCESSED_DIR/ "flood_labels"/ "master_event_anchors.csv")
CHIRPS_DAILY_FILE = (PROCESSED_DIR/ "weather"/ "chirps"/ "groundsource_antecedent_daily_rainfall.csv")
CHIRPS_FEATURE_FILE = (FEATURES_DIR/ "weather"/ "chirps"/ "groundsource_rainfall_features.csv")
ERA5_HOURLY_FILE = (PROCESSED_DIR/ "weather"/ "era5_land"/ "groundsource_era5_antecedent_hourly.parquet")
ERA5_FEATURE_FILE = (FEATURES_DIR/ "weather"/ "era5_land"/ "groundsource_meteorological_features.csv")
GIS_FEATURE_FILE = (FEATURES_DIR/ "environmental"/ "groundsource_environmental_features.csv")
ADMIN_JOIN_FILE = (PROCESSED_DIR/ "spatial"/ "administrative"/ "groundsource_administrative_joins.csv")

SUPPORTING_FILES = [
    PROCESSED_DIR/ "weather"/ "chirps"/ "chirps_archive_inventory.csv",

    PROCESSED_DIR/ "weather"/ "era5_land"/ "era5_land_inventory.csv",

    PROCESSED_DIR/ "environmental"/ "gis_raster_inventory.csv",

    PROCESSED_DIR/ "spatial"/ "administrative"/ "administrative_boundary_inventory.csv",
]

WORLDCOVER_CLASSES = {10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100}


#file validation
def validate_required_files() -> str:

    required_files = [
        LABEL_FILE,
        MASTER_ANCHOR_FILE,
        CHIRPS_DAILY_FILE,
        CHIRPS_FEATURE_FILE,
        ERA5_HOURLY_FILE,
        ERA5_FEATURE_FILE,
        GIS_FEATURE_FILE,
        ADMIN_JOIN_FILE,
        *SUPPORTING_FILES,
    ]

    missing_files = [str(path)
        for path in required_files
        if not path.exists()]

    if missing_files:
        raise FileNotFoundError("Missing ETL output files:\n" + "\n".join(missing_files))

    return (f"{len(required_files)} required files found")


#load datasets
def load_validation_datasets() -> dict[str, pd.DataFrame]:

    datasets = {
        "labels": pd.read_csv(LABEL_FILE),
        "master_anchors":pd.read_csv(MASTER_ANCHOR_FILE),
        "chirps_daily": pd.read_csv(CHIRPS_DAILY_FILE),
        "chirps_features": pd.read_csv(CHIRPS_FEATURE_FILE),
        "era5_hourly": pd.read_parquet(ERA5_HOURLY_FILE),
        "era5_features": pd.read_csv(ERA5_FEATURE_FILE),
        "gis_features": pd.read_csv(GIS_FEATURE_FILE),
        "admin_joins": pd.read_csv(ADMIN_JOIN_FILE),
    }

    return datasets

#base flood-label validation
def validate_labels(labels: pd.DataFrame) -> str:

    required_columns = {
        "label_record_id",
        "event_group_id",
        "start_date",
        "end_date",
        "latitude",
        "longitude",
        "flood_label",
        "verification_status",
    }

    missing_columns = (required_columns - set(labels.columns))

    if missing_columns:
        raise ValueError(f"Flood labels are missing columns: {sorted(missing_columns)}")

    if labels["label_record_id"].duplicated().any():
        raise ValueError("Duplicate flood label IDs detected")

    if labels["label_record_id"].isna().any():
        raise ValueError("Missing flood label IDs detected")

    if not set(labels["flood_label"].dropna().unique()).issubset({1}):
        raise ValueError("Unexpected flood-label values detected")

    valid_statuses = {"candidate", "near_match", "corroborated"}

    unexpected_statuses = (set(labels["verification_status"].dropna().unique()) - valid_statuses)

    if unexpected_statuses:
        raise ValueError(
            f"Unexpected verification statuses: {sorted(unexpected_statuses)}")

    start_dates = pd.to_datetime(labels["start_date"], errors="raise")
    end_dates = pd.to_datetime(labels["end_date"], errors="raise")
    invalid_dates = (end_dates < start_dates)

    if invalid_dates.any():
        raise ValueError("Flood labels contain end dates before start dates")

    if not labels["latitude"].between(-90, 90).all():
        raise ValueError("Invalid flood-label latitude detected")

    if not labels["longitude"].between(-180, 180).all():
        raise ValueError("Invalid flood-label longitude detected")

    return (f"{len(labels):,} flood-label rows valid")


#master anchor validation
def validate_master_anchors(anchors: pd.DataFrame) -> str:

    required_columns = {
        "label_record_id",
        "source_event_id",
        "event_group_id",
        "start_date",
        "end_date",
        "anchor_date",
        "flood_label",
        "verification_status",
        "record_role",
    }

    missing_columns = (required_columns - set(anchors.columns))

    if missing_columns:
        raise ValueError(f"Master event anchors are missing columns: {sorted(missing_columns)}")

    if anchors["label_record_id"].duplicated().any():
        raise ValueError("Duplicate master event anchors detected")

    if not set(anchors["flood_label"].dropna().unique()).issubset({1}):
        raise ValueError("Unexpected master-anchor flood labels")

    start_dates = pd.to_datetime(anchors["start_date"], errors="raise")
    end_dates = pd.to_datetime(anchors["end_date"], errors="raise")

    if (end_dates < start_dates).any():
        raise ValueError("Master anchor end date occurs before start date")

    return (f"{len(anchors):,} master anchors valid")


#component alignment
def validate_component_alignment(labels: pd.DataFrame, component: pd.DataFrame, component_name: str, date_column: str | None = None) -> str:

    if "label_record_id" not in component.columns:
        raise ValueError(f"{component_name} does not contain label_record_id")

    if component["label_record_id"].duplicated().any():
        raise ValueError(f"{component_name} contains duplicate label_record_id values")

    label_ids = set(labels["label_record_id"])
    component_ids = set(component["label_record_id"])
    missing_ids = (label_ids - component_ids)
    extra_ids = (component_ids - label_ids)

    if missing_ids or extra_ids:
        raise ValueError(f"{component_name} ID mismatch. Missing: {len(missing_ids)}, Extra: {len(extra_ids)}")

    baseline_columns = [
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status",
    ]

    baseline = labels[baseline_columns].copy()
    compare_columns = ["label_record_id"]

    for column in ["event_group_id", "latitude", "longitude", "verification_status"]:
        if column in component.columns:
            compare_columns.append(column)

    if (date_column is not None and date_column in component.columns):
        compare_columns.append(date_column)

    comparison = baseline.merge(component[compare_columns], on="label_record_id", how="inner", suffixes=("_label", "_component"), validate="one_to_one")

    if "event_group_id" in component.columns:
        left_groups = (comparison["event_group_id_label"].astype(str))
        right_groups = (comparison["event_group_id_component"].astype(str))

        if not left_groups.equals(right_groups):
            raise ValueError(f"{component_name} event-group alignment failed")

    if "verification_status" in component.columns:

        if not (comparison["verification_status_label"].astype(str) == comparison["verification_status_component"].astype(str)).all():
            raise ValueError(f"{component_name} verification-status alignment failed")

    if "latitude" in component.columns:

        if not np.allclose(comparison["latitude_label"].astype(float), comparison["latitude_component"].astype(float), atol=1e-6):
            raise ValueError(f"{component_name} latitude alignment failed")

    if "longitude" in component.columns:

        if not np.allclose(comparison["longitude_label"].astype(float), comparison["longitude_component"].astype(float), atol=1e-6):
            raise ValueError(f"{component_name} longitude alignment failed")

    if (date_column is not None and date_column in component.columns):
        label_dates = pd.to_datetime(comparison["start_date"], errors="raise").dt.date
        component_dates = pd.to_datetime(comparison[date_column], errors="raise").dt.date

        if not (label_dates == component_dates).all():
            raise ValueError(f"{component_name} target-date alignment failed")

    return (f"{component_name}: {len(component):,} rows aligned")


#CHIRPS validation
def validate_chirps_daily(daily: pd.DataFrame, labels: pd.DataFrame) -> str:

    required_columns = {
        "label_record_id",
        "target_date",
        "date",
        "days_before_target",
        "rainfall_mm",
        "chirps_sampling_method",
    }

    missing_columns = (required_columns - set(daily.columns))

    if missing_columns:
        raise ValueError(f"CHIRPS daily data is missing columns: {sorted(missing_columns)}")

    expected_rows = (len(labels) * 7)

    if len(daily) != expected_rows:
        raise ValueError(f"Unexpected CHIRPS daily row count. Expected {expected_rows:,}, found {len(daily):,}")

    if daily["rainfall_mm"].isna().any():
        raise ValueError("Missing CHIRPS daily rainfall detected")

    if (daily["rainfall_mm"] < 0).any():
        raise ValueError("Negative CHIRPS rainfall detected")

    expected_offsets = set(range(1, 8))

    for label_id, group in daily.groupby("label_record_id"):
        actual_offsets = set(group["days_before_target"].astype(int))

        if actual_offsets != expected_offsets:
            raise ValueError(f"Incomplete CHIRPS antecedent window for {label_id}")

    duplicate_days = daily.duplicated(subset=["label_record_id", "days_before_target"])

    if duplicate_days.any():
        raise ValueError("Duplicate CHIRPS antecedent days detected")

    return (f"{len(daily):,} CHIRPS daily rows valid")

def validate_chirps_features(features: pd.DataFrame, labels: pd.DataFrame) -> str:

    required_columns = {
        "label_record_id",
        "rain_1d",
        "rain_3d",
        "rain_7d",
    }

    missing_columns = (required_columns - set(features.columns))

    if missing_columns:
        raise ValueError(f"CHIRPS feature data is missing columns: {sorted(missing_columns)}")

    if features[["rain_1d", "rain_3d", "rain_7d"]].isna().any().any():
        raise ValueError("Missing CHIRPS rainfall features")

    if (features[["rain_1d", "rain_3d", "rain_7d"]] < 0).any().any():
        raise ValueError("Negative CHIRPS feature values detected")

    consistency = ((features["rain_1d"] <= features["rain_3d"]) & (features["rain_3d"] <= features["rain_7d"]))

    if not consistency.all():
        raise ValueError("CHIRPS accumulation consistency failed")

    return validate_component_alignment(labels, features, "CHIRPS features", date_column="target_date")


#ERA5 validation
def validate_era5_hourly(hourly: pd.DataFrame, labels: pd.DataFrame) -> str:

    required_columns = {
        "label_record_id",
        "timestamp",
        "temperature_c",
        "relative_humidity_pct",
        "pressure_hpa",
        "precipitation_mm",
        "swvl1",
        "swvl2",
    }

    missing_columns = (required_columns - set(hourly.columns))

    if missing_columns:
        raise ValueError(f"ERA5 hourly data is missing columns: {sorted(missing_columns)}")

    expected_rows = (len(labels) * 24)

    if len(hourly) != expected_rows:
        raise ValueError(f"Unexpected ERA5 hourly row count. Expected {expected_rows:,}, found {len(hourly):,}")

    required_values = [
        "temperature_c",
        "relative_humidity_pct",
        "pressure_hpa",
        "precipitation_mm",
        "swvl1",
        "swvl2",
    ]

    if hourly[required_values].isna().any().any():
        raise ValueError("Missing ERA5 hourly values detected")

    counts = (hourly.groupby("label_record_id").size())

    if not (counts == 24).all():
        raise ValueError("Not all flood observations have 24 ERA5 antecedent hours")

    if hourly.duplicated(subset=["label_record_id", "timestamp"]).any():
        raise ValueError("Duplicate ERA5 hourly timestamps detected")

    return (f"{len(hourly):,} ERA5 hourly rows valid")

def validate_era5_features(features: pd.DataFrame, labels: pd.DataFrame) -> str:

    feature_columns = [
        "temperature_mean_c",
        "temperature_min_c",
        "temperature_max_c",
        "relative_humidity_mean_pct",
        "pressure_mean_hpa",
        "era5_precip_1d_mm",
        "soil_moisture_layer1_mean",
        "soil_moisture_layer2_mean",
    ]

    missing_columns = (set(feature_columns) - set(features.columns))

    if missing_columns:
        raise ValueError(f"ERA5 features are missing columns: {sorted(missing_columns)}")

    if features[feature_columns].isna().any().any(): 
        raise ValueError("Missing ERA5 meteorological features")

    temperature_valid = ((features["temperature_min_c"] <= features["temperature_mean_c"]) & (features["temperature_mean_c"] <= features["temperature_max_c"]))

    if not temperature_valid.all():
        raise ValueError("ERA5 temperature consistency failed")

    if not features["relative_humidity_mean_pct"].between(0, 100).all():
        raise ValueError("ERA5 relative humidity outside 0-100 percent")

    if (features["pressure_mean_hpa"] <= 0).any():
        raise ValueError("Invalid ERA5 pressure values detected")

    if (features["era5_precip_1d_mm"] < 0).any():
        raise ValueError("Negative ERA5 precipitation detected")

    for column in ["soil_moisture_layer1_mean", "soil_moisture_layer2_mean"]:

        if not features[column].between(0, 1).all():
            raise ValueError(f"{column} outside expected 0-1 range")

    return validate_component_alignment(labels, features, "ERA5 features", date_column="target_date")


#GIS validation
def validate_gis_features(features: pd.DataFrame, labels: pd.DataFrame) -> str:

    required_columns = [
        "elevation_m",
        "slope_deg",
        "flood_hazard_score",
        "ghsl_built_surface_m2",
        "ghsl_built_up_fraction",
        "worldcover_2021_class",
    ]

    missing_columns = (set(required_columns) - set(features.columns))

    if missing_columns:
        raise ValueError(f"GIS features are missing columns: {sorted(missing_columns)}")

    if features[required_columns].isna().any().any():
        raise ValueError("Missing GIS/environmental features")

    if (features["slope_deg"] < 0).any():
        raise ValueError("Negative slope detected")

    if not features["flood_hazard_score"].between(0, 6).all():
        raise ValueError("ICPAC flood hazard outside expected 0-6 range")

    if not features["ghsl_built_surface_m2"].between(0, 10000).all():
        raise ValueError("GHSL built surface outside 0-10000 m2 range")

    if not features["ghsl_built_up_fraction"].between(0, 1).all():
        raise ValueError("GHSL built-up fraction outside 0-1 range")

    worldcover_values = set(features["worldcover_2021_class"].astype(int).unique())
    unexpected_classes = (worldcover_values - WORLDCOVER_CLASSES)

    if unexpected_classes:
        raise ValueError(f"Unexpected WorldCover classes: {sorted(unexpected_classes)}")

    return validate_component_alignment(labels, features, "GIS features", date_column="target_date")


#administrative validation
def validate_admin_joins(admin: pd.DataFrame, labels: pd.DataFrame) -> str:

    required_columns = {
        "label_record_id",
        "county_name",
        "subcounty_name",
        "ward_name",
    }

    missing_columns = (required_columns - set(admin.columns))

    if missing_columns:
        raise ValueError(f"Administrative joins are missing columns: {sorted(missing_columns)}")

    if admin[["county_name", "subcounty_name", "ward_name"]].isna().any().any():
        raise ValueError("Missing administrative assignments")

    county_values = (admin["county_name"].astype(str).str.strip().str.lower().unique())

    if set(county_values) != {"nairobi"}:
        raise ValueError(f"Administrative join contains unexpected counties: {county_values}")

    return validate_component_alignment(
        labels,
        admin,
        "Administrative joins",
        date_column="start_date")


#validation execution helper
def run_check(check_name: str, function, results: list[dict]) -> None:

    try:

        detail = function()

        results.append(
            {
                "check": check_name,
                "status": "PASS",
                "detail": detail,
            }
        )

    except Exception as error:

        results.append(
            {
                "check": check_name,
                "status": "FAIL",
                "detail": str(error),
            }
        )


#main validation pipeline
def run_etl_validation() -> pd.DataFrame:

    results = []

    run_check("required_files", validate_required_files, results)

    if results[-1]["status"] == "FAIL":
        return pd.DataFrame(results)

    datasets = (load_validation_datasets())
    labels = datasets["labels"]

    run_check("flood_labels", lambda: validate_labels(labels), results)
    run_check("master_event_anchors", lambda: validate_master_anchors(datasets["master_anchors"]), results)
    run_check("chirps_daily", lambda: validate_chirps_daily(datasets["chirps_daily"], labels, ), results)
    run_check("chirps_features", lambda: validate_chirps_features(datasets["chirps_features"], labels, ), results)
    run_check("era5_hourly", lambda: validate_era5_hourly(datasets["era5_hourly"], labels, ), results)
    run_check("era5_features", lambda: validate_era5_features(datasets["era5_features"], labels, ), results)
    run_check("gis_features", lambda: validate_gis_features( datasets["gis_features"], labels, ), results)
    run_check("administrative_joins", lambda: validate_admin_joins(datasets["admin_joins"], labels,),results)

    return pd.DataFrame(results)


#report writer
def save_validation_report(report: pd.DataFrame) -> None:

    VALIDATION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = (VALIDATION_OUTPUT_DIR/ "etl_validation_report.csv")
    report.to_csv(report_file, index=False)

    print(f"Saved ETL validation report: {report_file}")