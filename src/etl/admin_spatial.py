from pathlib import Path
import re
import geopandas as gpd
import pandas as pd
from config.paths import (SPATIAL_DIR, PROCESSED_DIR)


#configuration
ADMIN_OUTPUT_DIR = (PROCESSED_DIR/ "spatial"/ "administrative")
WGS84 = "EPSG:4326"
SUPPORTED_BOUNDARY_EXTENSIONS = {".shp", ".geojson", ".json", ".gpkg"}

#boundary discovery
def discover_boundary_files() -> dict[str, Path]:
    files = []

    for path in SPATIAL_DIR.rglob("*"):
        if (path.is_file() and path.suffix.lower() in SUPPORTED_BOUNDARY_EXTENSIONS):
            files.append(path)
    if not files:
        raise FileNotFoundError(f"No administrative boundary files found in {SPATIAL_DIR}")

    discovered = {}

    for path in files:

        text = str(path).lower()

        level = None

        if re.search(r"adm[_-]?1", text):
            level = "adm1"

        elif re.search(r"adm[_-]?2", text):
            level = "adm2"

        elif re.search(r"adm[_-]?3", text):
            level = "adm3"

        if level is None:
            continue

        if level in discovered:
            raise ValueError(
                f"Multiple boundary files found for {level}: "
                f"{discovered[level]} and {path}")

        discovered[level] = path

    missing_levels = ({"adm1", "adm2", "adm3"} - set(discovered))

    if missing_levels:
        raise ValueError(f"Missing administrative boundary levels: {sorted(missing_levels)}")

    return discovered


#name-column discovery
def identify_name_column(dataframe: gpd.GeoDataFrame, admin_level: str) -> str:
    candidate_columns = [
        "shapeName",
        "shapename",
        "name",
        "NAME",
        "Name",
        f"NAME_{admin_level[-1]}",
        f"name_{admin_level[-1]}",
        f"ADM{admin_level[-1]}_NAME",
        f"adm{admin_level[-1]}_name",
        "county",
        "subcounty",
        "sub_county",
        "constituency",
        "ward",
        "ward_name",
    ]

    for column in candidate_columns:
        if column in dataframe.columns:
            return column

    string_columns = [
        column
        for column in dataframe.columns
        if (column != dataframe.geometry.name and (pd.api.types.is_string_dtype(dataframe[column])
                or dataframe[column].dtype == "object"
            )
        )
    ]

    if not string_columns:
        raise ValueError(
            f"Could not identify administrative name "
            f"column for {admin_level}. "
            f"Available columns: "
            f"{list(dataframe.columns)}"
        )

    return string_columns[0]


#boundary loading
def load_boundary(file_path: Path, admin_level: str) -> gpd.GeoDataFrame:

    dataframe = gpd.read_file(file_path)

    if dataframe.empty:
        raise ValueError(f"{admin_level} boundary file is empty: {file_path}")

    if dataframe.crs is None:
        raise ValueError(f"{admin_level} boundary file has no CRS: {file_path}")

    dataframe = dataframe.to_crs(WGS84)
    name_column = identify_name_column(dataframe, admin_level)
    dataframe = dataframe[[name_column, dataframe.geometry.name]].copy()
    dataframe = dataframe.rename(columns={name_column:f"{admin_level}_name"})
    dataframe = dataframe[dataframe.geometry.notna()].copy()

    return dataframe.reset_index(drop=True)


#Nairobi ADM1 selection
def select_nairobi_county(adm1: gpd.GeoDataFrame) -> gpd.GeoDataFrame:

    names = (adm1["adm1_name"].astype(str).str.strip())
    nairobi = adm1[names.str.contains("nairobi", case=False, na=False)].copy()

    if len(nairobi) != 1:
        raise ValueError(f"Expected exactly one Nairobi ADM1 boundary. Found {len(nairobi)}.")

    return nairobi.reset_index(drop=True)

#load Groundsource locations
def load_admin_join_requests() -> gpd.GeoDataFrame:

    flood_label_file = (PROCESSED_DIR/ "flood_labels"/ "groundsource_positive_candidates.csv")
    if not flood_label_file.exists():
        raise FileNotFoundError(f"Processed Groundsource flood labels were not found: {flood_label_file}")

    dataframe = pd.read_csv(flood_label_file)

    required_columns = {
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status",
    }

    missing_columns = (required_columns - set(dataframe.columns))

    if missing_columns:
        raise ValueError(f"Groundsource administrative join input is missing columns: {sorted(missing_columns)}")

    if dataframe["label_record_id"].duplicated().any():
        raise ValueError("Duplicate label_record_id values found")

    points = gpd.GeoDataFrame(dataframe.copy(), geometry=gpd.points_from_xy(dataframe["longitude"], dataframe["latitude"]), crs=WGS84)

    return points


#clip lower-level boundaries to Nairobi
def filter_to_nairobi(boundaries: gpd.GeoDataFrame, nairobi_county: gpd.GeoDataFrame) -> gpd.GeoDataFrame:

    filtered = gpd.sjoin(boundaries, nairobi_county[["geometry"]], how="inner", predicate="intersects")
    filtered = filtered.drop(columns=[column 
                                      for column in ["index_right"]
                                      if column in filtered.columns])
    filtered = filtered.drop_duplicates(subset=[boundaries.geometry.name])

    return filtered.reset_index(drop=True)


#single administrative join
def join_admin_level(points: gpd.GeoDataFrame, boundaries: gpd.GeoDataFrame, admin_level: str) -> gpd.GeoDataFrame:

    result = gpd.sjoin(points, boundaries[[f"{admin_level}_name", "geometry"]], how="left", predicate="intersects")
    if result.index.duplicated().any():
        duplicates = result[result.index.duplicated(keep=False)]
        raise ValueError(f"Some points matched multiple {admin_level} polygons: {duplicates['label_record_id'].nunique()}")

    result = result.drop(
        columns=[column 
                 for column in ["index_right"] 
                 if column in result.columns])

    return result


#spatial join pipeline
def process_administrative_joins() -> tuple[pd.DataFrame, pd.DataFrame]:

    boundary_files = (discover_boundary_files())
    adm1 = load_boundary(boundary_files["adm1"], "adm1")
    adm2 = load_boundary(boundary_files["adm2"], "adm2")
    adm3 = load_boundary(boundary_files["adm3"], "adm3")
    nairobi_county = (select_nairobi_county(adm1))
    nairobi_adm2 = (filter_to_nairobi(adm2, nairobi_county))
    nairobi_adm3 = (filter_to_nairobi(adm3, nairobi_county))

    points = (load_admin_join_requests())

    joined = join_admin_level(points, nairobi_county, "adm1")
    joined = join_admin_level(joined, nairobi_adm2, "adm2")
    joined = join_admin_level(joined, nairobi_adm3, "adm3")
    joined = joined.rename(
        columns={
            "adm1_name":"county_name",
            "adm2_name":"subcounty_name",
            "adm3_name":"ward_name",
        }
    )

    required_admin_columns = [
        "county_name",
        "subcounty_name",
        "ward_name",
    ]

    missing_counts = (joined[required_admin_columns].isna().sum())

    if missing_counts.sum() > 0:
        raise ValueError(f"Administrative spatial joins contain unresolved locations: {missing_counts}")

    if len(joined) != len(points):
        raise ValueError("Administrative spatial join row count mismatch")
    
    if joined["label_record_id"].duplicated().any():
        raise ValueError("Duplicate administrative join rows generated")

    output_columns = [
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status",
        "county_name",
        "subcounty_name",
        "ward_name",
    ]

    administrative_data = (pd.DataFrame(joined[output_columns]).reset_index(drop=True))

    inventory = pd.DataFrame(
        [
            {
                "admin_level": "ADM1",
                "file_name":boundary_files["adm1"].name,
                "nairobi_features":len(nairobi_county),
            },
            {
                "admin_level": "ADM2",
                "file_name":boundary_files["adm2"].name,
                "nairobi_features":len(nairobi_adm2),
            },
            {
                "admin_level": "ADM3",
                "file_name":boundary_files["adm3"].name,
                "nairobi_features": len(nairobi_adm3),
            },
        ]
    )

    return (administrative_data, inventory)


#output writer
def save_administrative_join_outputs(administrative_data: pd.DataFrame, inventory: pd.DataFrame) -> None:

    ADMIN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    join_output = (ADMIN_OUTPUT_DIR/ "groundsource_administrative_joins.csv")
    inventory_output = (ADMIN_OUTPUT_DIR/ "administrative_boundary_inventory.csv")
    administrative_data.to_csv(join_output, index=False)
    inventory.to_csv(inventory_output, index=False)
    print(f"Saved administrative joins: {join_output}")
    print(f"Saved administrative boundary inventory: {inventory_output}")