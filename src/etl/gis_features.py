from pathlib import Path
import re
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform

from config.paths import(ENVIRONMENTAL_DIR, SPATIAL_DIR, FEATURES_DIR, PROCESSED_DIR)

#configuration
GIS_FEATURE_DIR = (FEATURES_DIR/"environmental")
GIS_PROCESSED_DIR = (PROCESSED_DIR/"environmental")

WGS84 = CRS.from_epsg(4326)

MAX_FALLBACK_RADIUS = 3
GHSL_EPOCHS = {2000, 2005, 2010, 2015, 2020, 2025}
WORLDCOVER_CLASSES = {10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100}

#raster discovery
def discover_all_rasters() -> list[Path]:
    rasters = []

    for root in [ENVIRONMENTAL_DIR, SPATIAL_DIR]:
        if not root.exists():
            continue

        rasters.extend(root.rglob("*.tif"))
        rasters.extend(root.rglob("*.tiff"))

    rasters = sorted(set(rasters))

    if not rasters:
        raise FileNotFoundError("No GIS/ environmental raster files were found")

    return rasters


def discover_gis_rasters() -> dict:
    rasters = discover_all_rasters()

    elevation_matches = [
        path for path in rasters
        if ("nasadem" in str(path).lower() and "elev" in path.name.lower())
    ]

    slope_matches = [
        path for path in rasters
        if ("slope" in path.name.lower())
    ]

    worldcover_matches = [
        path for path in rasters
        if ("worldcover" in str(path).lower())
    ]

    icpac_matches = [
        path for path in rasters
        if (
            "icpac" in str(path).lower()
            or (
                "flood" in path.name.lower()
                and "inundation" in path.name.lower()
            )
        )
    ]

    if len(elevation_matches) != 1:
        raise ValueError(
            f"Expected exactly one NASADEM elevation raster found: "
            f"{[path.name for path in elevation_matches]}"
        )

    if len(slope_matches) != 1:
        raise ValueError(
            f"Expected exactly one slope raster found: "
            f"{[path.name for path in slope_matches]}"
        )

    if len(worldcover_matches) != 1:
        raise ValueError(
            f"Expected exactly one WorldCover raster found: "
            f"{[path.name for path in worldcover_matches]}"
        )

    if len(icpac_matches) != 1:
        raise ValueError(
            f"Expected exactly one ICPAC flood raster found: "
            f"{[path.name for path in icpac_matches]}"
        )

    ghsl_files = {}

    for path in rasters:
        path_text = str(path).lower()

        if ("ghsl" not in path_text and "ghs_built" not in path_text):
            continue

        year_match = re.search(
            r"(2000|2005|2010|2015|2020|2025)",
            path.name
        )

        if year_match is None:
            continue

        year = int(year_match.group(1))

        if year in ghsl_files:
            raise ValueError(f"Multiple GHSL rasters found for {year}")

        ghsl_files[year] = path

    missing_epochs = (GHSL_EPOCHS - set(ghsl_files))

    if missing_epochs:
        raise ValueError(f"Missing GHSL epochs: {sorted(missing_epochs)}")

    return {
        "elevation": elevation_matches[0],
        "slope": slope_matches[0],
        "worldcover": worldcover_matches[0],
        "icpac": icpac_matches[0],
        "ghsl": dict(sorted(ghsl_files.items()))
    }


#load Groundsource requests
def load_gis_feature_requests() -> pd.DataFrame:

    flood_label_file = (
        PROCESSED_DIR/"flood_labels"/"groundsource_positive_candidates.csv"
    )

    if not flood_label_file.exists():
        raise FileNotFoundError(
            f"Processed Groundsource flood labels were not found: "
            f"{flood_label_file}"
        )

    dataframe = pd.read_csv(flood_label_file)

    required_columns = {
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status"
    }

    missing_columns = (required_columns - set(dataframe.columns))

    if missing_columns:
        raise ValueError(
            f"Groundsource feature requests are missing columns: "
            f"{sorted(missing_columns)}"
        )

    if dataframe["label_record_id"].duplicated().any():
        raise ValueError("Duplicated label_record_id values found")

    dataframe["target_date"] = (
        pd.to_datetime(
            dataframe["start_date"],
            errors="raise"
        ).dt.date
    )

    return dataframe[
        [
            "label_record_id",
            "event_group_id",
            "target_date",
            "latitude",
            "longitude",
            "verification_status"
        ]
    ].copy()


#coordinate handling
def coordinate_to_raster(
    dataset,
    longitude: float,
    latitude: float
) -> tuple[float, float]:

    if dataset.crs is None:
        raise ValueError("Raster does not define a CRS")

    if dataset.crs == WGS84:
        return (longitude, latitude)

    x_values, y_values = transform(
        WGS84,
        dataset.crs,
        [longitude],
        [latitude]
    )

    return (x_values[0], y_values[0])


#single raster sampling
def sample_raster_value(
    dataset,
    longitude: float,
    latitude: float,
    valid_values: set[int] | None = None,
    minimum_value: float | None = None,
    max_search_radius: int = MAX_FALLBACK_RADIUS
) -> tuple[float, str, int]:

    x, y = coordinate_to_raster(
        dataset,
        longitude,
        latitude
    )

    row, col = dataset.index(x, y)

    def valid(value) -> bool:
        if np.ma.is_masked(value):
            return False

        if not np.isfinite(float(value)):
            return False

        if (
            valid_values is not None
            and int(value) not in valid_values
        ):
            return False

        if (
            minimum_value is not None
            and float(value) < minimum_value
        ):
            return False

        return True

    if (
        0 <= row < dataset.height
        and 0 <= col < dataset.width
    ):
        sample = dataset.read(
            1,
            window=(
                (row, row + 1),
                (col, col + 1)
            ),
            masked=True
        )

        value = sample[0, 0]

        if valid(value):
            return (float(value), "direct", 0)

    for radius in range(
        1,
        max_search_radius + 1
    ):
        row_min = max(row - radius, 0)
        row_max = min(row + radius, dataset.height - 1)
        col_min = max(col - radius, 0)
        col_max = min(col + radius, dataset.width - 1)

        candidates = []

        for candidate_row in range(
            row_min,
            row_max + 1
        ):
            for candidate_col in range(
                col_min,
                col_max + 1
            ):

                if max(
                    abs(candidate_row - row),
                    abs(candidate_col - col)
                ) != radius:
                    continue

                sample = dataset.read(
                    1,
                    window=(
                        (
                            candidate_row,
                            candidate_row + 1
                        ),
                        (
                            candidate_col,
                            candidate_col + 1
                        )
                    ),
                    masked=True
                )

                value = sample[0, 0]

                if not valid(value):
                    continue

                distance = (
                    (
                        candidate_row - row
                    ) ** 2
                    +
                    (
                        candidate_col - col
                    ) ** 2
                ) ** 0.5

                candidates.append(
                    (
                        distance,
                        float(value)
                    )
                )

        if candidates:
            candidates.sort(
                key=lambda item: item[0]
            )

            return (
                candidates[0][1],
                "nearest_valid",
                radius
            )

    return (
        np.nan,
        "missing",
        max_search_radius
    )


#static raster extraction
def extract_static_raster(
    requests: pd.DataFrame,
    raster_path: Path,
    output_column: str,
    valid_values: set[int] | None = None,
    minimum_value: float | None = None,
) -> pd.DataFrame:

    dataframe = requests.copy()

    values = []
    methods = []
    fallback_distances = []

    with rasterio.open(raster_path) as dataset:

        for row in dataframe.itertuples(index=False):

            (
                value,
                method,
                fallback_distance
            ) = sample_raster_value(
                dataset=dataset,
                longitude=float(row.longitude),
                latitude=float(row.latitude),
                valid_values=valid_values,
                minimum_value=minimum_value,
            )

            values.append(value)
            methods.append(method)
            fallback_distances.append(fallback_distance)

    dataframe[output_column] = values
    dataframe[f"{output_column}_sampling_method"] = methods
    dataframe[f"{output_column}_fallback_pixels"] = fallback_distances

    return dataframe

#ICPAC-specific extractor
def extract_icpac_raster(requests: pd.DataFrame, raster_path: Path) -> pd.DataFrame:

    dataframe = requests.copy()

    values = []
    methods = []
    fallback_distances = []

    with rasterio.open(raster_path) as dataset:

        raw_values = dataset.read(1, masked=False)

        finite_values = raw_values[np.isfinite(raw_values)]
        unique_values = np.unique(finite_values)

        positive_values = unique_values[unique_values > 0]

        if len(positive_values) > 0:
            if not np.allclose(positive_values % 20, 0, atol=1e-4):
                raise ValueError("Unexpected ICPAC raster encoding. " f"Values are not multiples of 20: " f"{positive_values[:20]}")

            normalized_values = (positive_values / 20)
            if normalized_values.max() > 6:
                raise ValueError("Unexpected ICPAC normalized hazard " f"score above 6: " f"{normalized_values.max()}")

        for row in dataframe.itertuples(index=False):
            x, y = coordinate_to_raster(dataset, float(row.longitude), float(row.latitude))
            raster_row, raster_col = dataset.index(x, y)

            if not (0 <= raster_row < dataset.height and 0 <= raster_col < dataset.width):
                values.append(np.nan)
                methods.append("outside_raster")
                fallback_distances.append(0)
                continue

            sample = dataset.read(
                1,
                window=(
                    (
                        raster_row,
                        raster_row + 1
                    ),
                    (
                        raster_col,
                        raster_col + 1
                    )
                ),
                masked=False
            )

            raw_value = float(
                sample[0, 0]
            )

            if (
                not np.isfinite(raw_value)
                or raw_value <= 0
            ):
                values.append(0.0)
                methods.append("dry_or_nodata")
                fallback_distances.append(0)
                continue

            hazard_score = (
                raw_value / 20.0
            )

            if (
                hazard_score < 0
                or hazard_score > 6
            ):
                raise ValueError(
                    "Unexpected normalized ICPAC "
                    f"hazard score: {hazard_score}"
                )

            values.append(
                hazard_score
            )

            methods.append(
                "direct_normalized"
            )

            fallback_distances.append(
                0
            )

    dataframe[
        "flood_hazard_score"
    ] = values

    dataframe[
        "flood_hazard_score_sampling_method"
    ] = methods

    dataframe[
        "flood_hazard_score_fallback_pixels"
    ] = fallback_distances

    return dataframe

#GHSL epoch selection
def select_ghsl_epoch(target_date) -> int:

    year = target_date.year

    valid_epochs = [
        epoch
        for epoch in GHSL_EPOCHS
        if epoch <= year
    ]

    if not valid_epochs:
        raise ValueError(
            f"No historical GHSL epoch available for {year}"
        )

    return max(valid_epochs)


#GHSL extraction
def extract_ghsl_features(
    requests: pd.DataFrame,
    ghsl_files: dict[int, Path]
) -> pd.DataFrame:

    dataframe = requests.copy()

    dataframe["ghsl_epoch"] = (
        dataframe["target_date"]
        .map(select_ghsl_epoch)
    )

    dataframe["ghsl_built_surface_m2"] = np.nan
    dataframe["ghsl_sampling_method"] = None

    dataframe["ghsl_fallback_pixels"] = pd.Series(
        pd.NA,
        index=dataframe.index,
        dtype="Int64"
    )

    for epoch, epoch_rows in dataframe.groupby(
        "ghsl_epoch"
    ):
        raster_path = ghsl_files[int(epoch)]

        with rasterio.open(raster_path) as dataset:

            for row in epoch_rows.itertuples():

                (
                    value,
                    method,
                    fallback_distance
                ) = sample_raster_value(
                    dataset=dataset,
                    longitude=float(row.longitude),
                    latitude=float(row.latitude),
                    minimum_value=0,
                )

                dataframe.loc[
                    row.Index,
                    "ghsl_built_surface_m2"
                ] = value

                dataframe.loc[
                    row.Index,
                    "ghsl_sampling_method"
                ] = method

                dataframe.loc[
                    row.Index,
                    "ghsl_fallback_pixels"
                ] = fallback_distance

    dataframe["ghsl_built_up_fraction"] = (
        dataframe["ghsl_built_surface_m2"]
        / 10000.0
    ).clip(
        lower=0,
        upper=1
    )

    return dataframe


#feature extraction pipeline
def process_gis_features() -> tuple[
    pd.DataFrame,
    pd.DataFrame
]:

    rasters = discover_gis_rasters()
    requests = load_gis_feature_requests()

    features = requests.copy()

    features = extract_static_raster(
        features,
        rasters["elevation"],
        "elevation_m"
    )

    features = extract_static_raster(
        features,
        rasters["slope"],
        "slope_deg",
        minimum_value=0
    )

    features = extract_icpac_raster(
        features,
        rasters["icpac"]
    )

    features = extract_static_raster(
        features,
        rasters["worldcover"],
        "worldcover_2021_class",
        valid_values=WORLDCOVER_CLASSES
    )

    features = extract_ghsl_features(
        features,
        rasters["ghsl"]
    )

    required_feature_columns = [
        "elevation_m",
        "slope_deg",
        "flood_hazard_score",
        "ghsl_built_surface_m2",
        "ghsl_built_up_fraction",
        "worldcover_2021_class",
    ]

    missing_counts = (
        features[
            required_feature_columns
        ]
        .isna()
        .sum()
    )

    if missing_counts.sum() > 0:
        raise ValueError(
            "GIS feature extraction contains unresolved "
            f"missing values: {missing_counts}"
        )

    if len(features) != len(requests):
        raise ValueError(
            "GIS feature row count mismatch"
        )

    if features[
        "label_record_id"
    ].duplicated().any():
        raise ValueError(
            "Duplicate GIS feature rows generated"
        )

    inventory_records = []

    source_paths = {
        "nasadem_elevation":
            rasters["elevation"],

        "nasadem_slope":
            rasters["slope"],

        "icpac_flood_hazard":
            rasters["icpac"],

        "worldcover_2021":
            rasters["worldcover"],
    }

    for epoch, path in rasters["ghsl"].items():
        source_paths[
            f"ghsl_{epoch}"
        ] = path

    for source_name, path in source_paths.items():

        with rasterio.open(path) as dataset:

            inventory_records.append(
                {
                    "source": source_name,
                    "file_name": path.name,
                    "crs": str(dataset.crs),
                    "width": dataset.width,
                    "height": dataset.height,
                    "band_count": dataset.count,
                    "nodata": dataset.nodata,
                    "dtype": dataset.dtypes[0],
                }
            )

    inventory = pd.DataFrame(
        inventory_records
    )

    return (
        features,
        inventory
    )


#output writer
def save_gis_feature_outputs(
    features: pd.DataFrame,
    inventory: pd.DataFrame
) -> None:

    GIS_FEATURE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    GIS_PROCESSED_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    feature_output = (
        GIS_FEATURE_DIR
        / "groundsource_environmental_features.csv"
    )

    inventory_output = (
        GIS_PROCESSED_DIR
        / "gis_raster_inventory.csv"
    )

    features.to_csv(
        feature_output,
        index=False
    )

    inventory.to_csv(
        inventory_output,
        index=False
    )

    print(
        f"Saved environmental features: "
        f"{feature_output}"
    )

    print(
        f"Saved GIS raster inventory: "
        f"{inventory_output}"
    )