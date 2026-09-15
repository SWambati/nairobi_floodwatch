from pathlib import Path
from tempfile import mkdtemp
import zipfile

import numpy as np
import pandas as pd

from config.paths import (ERA5_LAND_DIR, PROCESSED_DIR)

#configuration
ERA5_OUTPUT_DIR =(PROCESSED_DIR/"weather"/"era5_land")
EXPECTED_START_TIME = pd.Timestamp("2004-01-01 00:00:00", tz="UTC")
EXPECTED_END_TIME = pd.Timestamp("2025-12-31 23:00:00", tz="UTC")
EXPECTED_GRID_POINTS = 8
EXPECTED_HOURS_PER_GRID_POINT = 192_864
EXPECTED_TOTAL_ROWS = (EXPECTED_GRID_POINTS * EXPECTED_HOURS_PER_GRID_POINT)
ERA5_VARIABLE_GROUPS = {
    "temperature_dewpoint": {"t2m", "d2m"},
    "pressure_precipitation": {"sp", "tp"},
    "soil_moisture": {"swvl1", "swvl2"},
}
ALL_ERA5_VARIABLES = {"t2m", "d2m", "sp", "tp", "swvl1", "swvl2"}

#column handling
def normalize_column_names(dataframe: pd.DataFrame) -> pd.DataFrame:

    #normalize CSV column names without modifying data

    dataframe = dataframe.copy()

    dataframe.columns = [str(column).strip().lower() for column in dataframe.columns]

    return dataframe

def find_column(columns: set[str], candidates: list[str], column_type: str) -> str:

    #reslve one canonical column from possible ERA5/CDS column names

    for candidate in candidates:
        if candidate in columns:
            return candidate

    raise ValueError(f"Could not identify ERA5 {column_type} column. Available columns: {sorted(columns)}")

#file discovery
def discover_era5_files() -> dict[str, Path]:

    #discover ERA5-LAND CSV files based on variables contained in each files

    csv_files = sorted(ERA5_LAND_DIR.rglob("*.csv"))

    #ERA5-Land source files may still be stored inside the
    #original CDS ZIP archive. Extract them temporarily for processing.
    if not csv_files:
        zip_files = sorted(ERA5_LAND_DIR.rglob("*.zip"))

        if zip_files:
            extraction_root = Path(mkdtemp(prefix="era5_land_"))

            for zip_file in zip_files:
                target_directory = extraction_root / zip_file.stem
                target_directory.mkdir(parents=True, exist_ok=True)

                with zipfile.ZipFile(zip_file, "r") as archive:
                    archive.extractall(target_directory)

            csv_files = sorted(extraction_root.rglob("*.csv"))

    if not csv_files:
        raise FileNotFoundError(f"No ERA5-Land CSV files were found in {ERA5_LAND_DIR}")

    discovered: dict[str, Path] = {}

    for file_path in csv_files:
        header = pd.read_csv(file_path, nrows=0)
        header = normalize_column_names(header)
        columns = set(header.columns)

        for (group_name, variables) in ERA5_VARIABLE_GROUPS.items():
            if variables.issubset(columns):
                 if (group_name in discovered and discovered[group_name] != file_path):
                     raise ValueError("Multiple ERA5-Land files "f"match group '{group_name}':" f"{discovered[group_name].name}, {file_path.name}")

                 discovered[group_name] = file_path

    missing_groups = (set(ERA5_VARIABLE_GROUPS) - set(discovered))

    if missing_groups:
        raise ValueError(f"Could not find ERA5-Land files for variable groups: {sorted(missing_groups)}")

    return discovered

#parameter file loading

def load_era5_parameter_file(file_path: Path, variables: set[str]) -> pd.DataFrame:

    #Load an ERA5-Land parameter CSV into the canonical key structure: timestamp, latitude, longitude, ERA5 variables

    dataframe = pd.read_csv(file_path)
    dataframe = normalize_column_names(dataframe)
    columns = set(dataframe.columns)
    timestamp_column = find_column(columns, ["timestamp", "valid_time", "time", "datetime", "date"], "timestamp")

    latitude_column = find_column(columns, ["latitude", "lat"], "latitude")
    longitude_column = find_column(columns, ["longitude", "lon", "lng"], "longitude")
    missing_variables = (variables - columns)

    if missing_variables:
        raise ValueError(f"{file_path.name} is missing variables: {sorted(missing_variables)}")

    selected_columns = [timestamp_column, latitude_column, longitude_column, *sorted(variables)]
    dataframe = dataframe[selected_columns].copy()
    dataframe = dataframe.rename(columns={timestamp_column: "timestamp", latitude_column: "latitude", longitude_column: "longitude"})

    dataframe["timestamp"] = pd.to_datetime(dataframe["timestamp"], errors="raise", utc=True)
    numeric_columns = ["latitude", "longitude", *sorted(variables)]

    for column in numeric_columns:
        dataframe[column] = pd.to_numeric(dataframe[column], errors="raise")

    key_columns = ["timestamp", "latitude", "longitude"]

    if dataframe.duplicated(subset=key_columns).any():
        raise ValueError(f"{file_path.name} contains duplicate timestamp or grid-point rows")
    return (dataframe.sort_values(key_columns).reset_index(drop=True))

#archive loading
def load_era5_archive() -> tuple[pd.DataFrame, pd.DataFrame]:

    #load and merge the ERA5-Land parameter groups
    # No unit conversion/ feature deriviation occurs here

    files = discover_era5_files()
    group_dataframes = {}
    inventory_records =[]

    for (group_name, variables) in ERA5_VARIABLE_GROUPS.items():
        file_path = files[group_name]
        dataframe = (load_era5_parameter_file(file_path, variables))

        group_dataframes[group_name] = dataframe

        inventory_records.append(
            {
                "parameter_group": group_name,
                "file_name": file_path.name,
                "variables": ", ".join(sorted(variables)),
                "row_count": len(dataframe),
                "grid_points": dataframe[["latitude", "longitude"]].drop_duplicates().shape[0],
                "first_timestamp": dataframe["timestamp"].min(),
                "last_timestamp": dataframe["timestamp"].max(),
                "missing_values": int(dataframe[list(variables)].isna().sum().sum())
            }
        )

    key_columns = ["timestamp", "latitude", "longitude"]

    merged = group_dataframes["temperature_dewpoint"].merge(group_dataframes["pressure_precipitation"], on=key_columns, how="outer", validate="one_to_one")

    merged = merged.merge(group_dataframes["soil_moisture"], on=key_columns, how="outer", validate="one_to_one")
    merged = (merged.sort_values(key_columns).reset_index(drop=True))
    inventory = pd.DataFrame(inventory_records)
    return (merged, inventory)

#archive validation
def validate_era5_archive(dataframe: pd.DataFrame) -> None:

    #validate temporal completeness, grid coverage, duplicates and missing ERA5 variables

    key_columns = ["timestamp", "latitude", "longitude"]
    missing_columns = (ALL_ERA5_VARIABLES | set(key_columns)) - set(dataframe.columns)

    if missing_columns:
        raise ValueError(f"Merged ERA5-Land archive is missing columns: {sorted(missing_columns)}")

    if dataframe.duplicated(subset=key_columns).any():
        raise ValueError("Merged ERA5-Land archive contains duplicate timestamp or grid-point rows")

    grid_points = (dataframe[["latitude", "longitude"]].drop_duplicates())

    if len(grid_points) != EXPECTED_GRID_POINTS:
        raise ValueError(f"Unexpected ERA5_Land grid-point count. Expected {EXPECTED_GRID_POINTS}, found {len(grid_points)}.")

    if len(dataframe) != EXPECTED_TOTAL_ROWS:
        raise ValueError(f"Unexpected ERA5-Land row count. Expected {EXPECTED_TOTAL_ROWS}, found {len(dataframe):,}.")

    minimum_timestamp = (dataframe["timestamp"].min())
    maximum_timestamp = (dataframe["timestamp"].max())

    if (minimum_timestamp != EXPECTED_START_TIME):
        raise ValueError(f"Unexpected ERA5-Land start time: {minimum_timestamp}")
    if (maximum_timestamp != EXPECTED_END_TIME):
        raise ValueError(f"Unexpected ERA5-Land end time: {maximum_timestamp}")

    missing_values = (dataframe[sorted(ALL_ERA5_VARIABLES)].isna().sum())

    if missing_values.sum() > 0:
        raise ValueError(f"ERA5-Land archive contains missing meteorological values: {missing_values}")

    rows_per_grid = (dataframe.groupby(["latitude", "longitude"]).size())
    invalid_grid_counts = (rows_per_grid[rows_per_grid != EXPECTED_HOURS_PER_GRID_POINT])

    if not invalid_grid_counts.empty:
        raise ValueError(f"One or more ERA5-Land grid points have incomplete hourly coverage: {invalid_grid_counts}")

#distance calculation
def haversine_distance_km(latitude:float, longitude:float, grid_latitudes: np.ndarray, grid_longitudes: np.ndarray) -> np.ndarray:

    #calculate sphere distances from on Nairobi location to each ERA5-Land grid point

    earth_radius_km = 6371.0088

    latitude_1 = np.radians(latitude)
    longitude_1 = np.radians(longitude)
    latitude_2 = np.radians(grid_latitudes)
    longitude_2 = np.radians(grid_longitudes)
    delta_latitude = (latitude_2 -latitude_1)
    delta_longitude = (longitude_2 - longitude_1)

    a = (np.sin (delta_latitude / 2)**2 + np.cos(latitude_1)*np.cos(latitude_2)*np.sin(delta_longitude / 2)**2)
    c = (2 * np.arcsin(np.sqrt(a)))

    return (earth_radius_km * c)

#Groundsource to ERA5-Land Grid Mapping
def build_groundsource_grid_mapping( era5_data: pd.DataFrame) -> pd.DataFrame:

    #map individual groundsource location to the nearest acquired ERA5-Land grid point
    #Event-time meteoroloical features are not yet extracted/ aggregated

    flood_label_file = (PROCESSED_DIR/"flood_labels"/"groundsource_positive_candidates.csv")

    if not flood_label_file.exists():
        raise FileNotFoundError(f"Processed Groundsource flood labels were not found. Run {flood_label_file}")

    labels = pd.read_csv(flood_label_file)
    required_columns = {
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status"
    }

    missing_columns = (required_columns - set(labels.columns))

    if missing_columns:
        raise ValueError(f"Groundsource labels are missing columns: {sorted(missing_columns)}")

    grid_points = (era5_data[["latitude", "longitude"]].drop_duplicates().sort_values(["latitude", "longitude"]).reset_index(drop=True))

    grid_latitudes = (grid_points["latitude"].to_numpy(dtype=float))
    grid_longitudes = (grid_points["longitude"].to_numpy(dtype=float))

    mappings = []

    for row in labels.itertuples(index=False):
        distances = (haversine_distance_km(float(row.latitude), float(row.longitude), grid_latitudes, grid_longitudes))
        nearest_index = int(np.argmin(distances))

        mappings.append(
            {
                "label_record_id": row.label_record_id,
                "event_group_id": row.event_group_id,
                "start_date": row.start_date,
                "latitude": float(row.latitude),
                "longitude": float(row.longitude),
                "verification_status": row.verification_status,
                "era5_latitude": float(grid_latitudes[nearest_index]),
                "era5_longitude": float(grid_longitudes[nearest_index]),
                "era5_distance_km": float(distances[nearest_index])
            }
        )
    mapping = pd.DataFrame(mappings)

    if mapping["label_record_id"].duplicated().any():
        raise ValueError("Duplicate Groundsource rows were generated during ERA5 grid mapping")

    if len(mapping) != len(labels):
        raise ValueError("Groundsource to ERA5 mapping row count mismatch")

    return mapping

#pipeline
def process_era5_land() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    #Run ERA5-Land preprocessing. This returns canonical hourly ERA5 archive and Groundsource to ERA5 grid mapping archive inventory

    era5_data, inventory = (load_era5_archive())
    validate_era5_archive(era5_data)
    grid_mapping = (build_groundsource_grid_mapping(era5_data))

    return (era5_data, grid_mapping, inventory)

#output writer
def save_era5_outputs(era5_data: pd.DataFrame, grid_mapping:pd.DataFrame, inventory: pd.DataFrame) -> None:

    #Persist ERA5-Land outputs

    ERA5_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    hourly_output = (ERA5_OUTPUT_DIR/"era5_land_hourly_canonical.parquet")
    mapping_output = (ERA5_OUTPUT_DIR/"groundsource_era5_grid_mapping.csv")
    inventory_output = (ERA5_OUTPUT_DIR/"era5_land_inventory.csv")

    era5_data.to_parquet(hourly_output, index=False)
    grid_mapping.to_csv(mapping_output, index=False)
    inventory.to_csv(inventory_output, index=False)

    print(f"Saved canonical ERA5-Land archive: {hourly_output}")
    print(f"Saved Groundsource to ERA5 grid mapping: {mapping_output}")
    print(f"Saved ERA5-Land inventory: {inventory_output}")