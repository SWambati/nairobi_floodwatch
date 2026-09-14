from datetime import date, timedelta
from pathlib import Path
import re

import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform

from config.paths import (
    CHIRPS_DIR,
    PROCESSED_DIR,
)

# CONFIGURATION

CHIRPS_FILE_PATTERN = (
    "chirps_v3_sat_nairobi_*.tif"
)

CHIRPS_OUTPUT_DIR = (
    PROCESSED_DIR
    / "weather"
    / "chirps"
)

EXPECTED_START_DATE = date(
    2004,
    1,
    1,
)

EXPECTED_END_DATE = date(
    2026,
    7,
    31,
)

WGS84 = CRS.from_epsg(4326)

# Search up to three CHIRPS grid cells away when an exact-boundary crop has left the requested cell as NoData.
MAX_FALLBACK_RADIUS = 3

# FILE DISCOVERY

def discover_chirps_files() -> dict[int, Path]:
    """
    Discover annual Nairobi CHIRPS GeoTIFFs and map
    each file to its corresponding year.
    """

    files = sorted(
        CHIRPS_DIR.glob(
            CHIRPS_FILE_PATTERN
        )
    )

    if not files:
        raise FileNotFoundError(
            "No CHIRPS GeoTIFF files were found in "
            f"{CHIRPS_DIR}"
        )

    year_pattern = re.compile(
        r"(20\d{2})"
    )

    year_files: dict[int, Path] = {}

    for file_path in files:
        match = year_pattern.search(
            file_path.stem
        )

        if match is None:
            raise ValueError(
                "Could not determine year from "
                f"CHIRPS filename: {file_path.name}"
            )

        year = int(
            match.group(1)
        )

        if year in year_files:
            raise ValueError(
                "Multiple CHIRPS files found for "
                f"year {year}."
            )

        year_files[year] = file_path

    return dict(
        sorted(
            year_files.items()
        )
    )

# BAND-DATE HANDLING

def _parse_band_date(
    description: str | None,
) -> date | None:
    """
    Parse a date from an Earth Engine-style CHIRPS
    band description.

    """

    if not description:
        return None

    match = re.search(
        r"(\d{4})[_-](\d{2})[_-](\d{2})",
        description,
    )

    if match is None:
        return None

    return date(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )


def get_band_dates(
    dataset: rasterio.io.DatasetReader,
    year: int,
) -> list[date]:
    """
    Determine the calendar date represented by every
    band in one annual CHIRPS GeoTIFF.

    Band descriptions are used where available.

    If descriptions are unavailable, dates are derived
    sequentially from January 1 because the acquisition
    was exported as one continuous daily band per day.
    """

    descriptions = (
        dataset.descriptions
        or ()
    )

    parsed_dates = [
        _parse_band_date(description)
        for description in descriptions
    ]

    if (
        len(parsed_dates) == dataset.count
        and all(
            parsed_date is not None
            for parsed_date in parsed_dates
        )
    ):
        dates = parsed_dates

    else:
        dates = [
            date(year, 1, 1)
            + timedelta(days=index)
            for index in range(
                dataset.count
            )
        ]

    if len(set(dates)) != len(dates):
        raise ValueError(
            f"Duplicate CHIRPS band dates detected "
            f"for year {year}."
        )

    if any(
        band_date.year != year
        for band_date in dates
    ):
        raise ValueError(
            f"CHIRPS band dates do not match "
            f"filename year {year}."
        )

    return dates

# ARCHIVE INVENTORY


def build_chirps_inventory() -> pd.DataFrame:
    """
    Inspect every annual CHIRPS raster and return
    archive metadata.
    """

    year_files = (
        discover_chirps_files()
    )

    records = []

    for year, file_path in year_files.items():

        with rasterio.open(
            file_path
        ) as dataset:

            band_dates = get_band_dates(
                dataset,
                year,
            )

            records.append(
                {
                    "year": year,
                    "file_name":
                        file_path.name,

                    "band_count":
                        dataset.count,

                    "first_date":
                        min(band_dates),

                    "last_date":
                        max(band_dates),

                    "crs":
                        str(dataset.crs),

                    "width":
                        dataset.width,

                    "height":
                        dataset.height,

                    "resolution_x":
                        abs(
                            dataset.transform.a
                        ),

                    "resolution_y":
                        abs(
                            dataset.transform.e
                        ),

                    "nodata":
                        dataset.nodata,

                    "dtype":
                        dataset.dtypes[0],
                }
            )

    return (
        pd.DataFrame(records)
        .sort_values("year")
        .reset_index(drop=True)
    )


def validate_chirps_archive() -> pd.DataFrame:
    """
    Validate that the finalized CHIRPS acquisition
    contains complete daily coverage from
    2004-01-01 through 2026-07-31.
    """

    year_files = (
        discover_chirps_files()
    )

    expected_years = set(
        range(
            EXPECTED_START_DATE.year,
            EXPECTED_END_DATE.year + 1,
        )
    )

    actual_years = set(
        year_files.keys()
    )

    missing_years = (
        expected_years
        - actual_years
    )

    unexpected_years = (
        actual_years
        - expected_years
    )

    if missing_years:
        raise ValueError(
            "CHIRPS archive is missing years: "
            f"{sorted(missing_years)}"
        )

    if unexpected_years:
        raise ValueError(
            "CHIRPS archive contains unexpected "
            f"years: {sorted(unexpected_years)}"
        )

    all_dates = []

    for year, file_path in year_files.items():

        with rasterio.open(
            file_path
        ) as dataset:

            if dataset.crs is None:
                raise ValueError(
                    "CHIRPS raster does not define "
                    f"a CRS: {file_path.name}"
                )

            band_dates = get_band_dates(
                dataset,
                year,
            )

            all_dates.extend(
                band_dates
            )

    if len(set(all_dates)) != len(all_dates):
        raise ValueError(
            "Duplicate dates exist across "
            "CHIRPS annual rasters."
        )

    expected_dates = set(
        pd.date_range(
            EXPECTED_START_DATE,
            EXPECTED_END_DATE,
            freq="D",
        ).date
    )

    actual_dates = set(
        all_dates
    )

    missing_dates = (
        expected_dates
        - actual_dates
    )

    unexpected_dates = (
        actual_dates
        - expected_dates
    )

    if missing_dates:
        raise ValueError(
            "CHIRPS archive is missing "
            f"{len(missing_dates):,} daily bands."
        )

    if unexpected_dates:
        raise ValueError(
            "CHIRPS archive contains "
            f"{len(unexpected_dates):,} "
            "unexpected dates."
        )

    return build_chirps_inventory()


# REQUEST VALIDATION

def validate_location_date_requests(
    dataframe: pd.DataFrame,
    date_column: str,
    latitude_column: str,
    longitude_column: str,
) -> None:
    """
    Validate coordinate/date requests supplied to the
    CHIRPS extraction module.
    """

    required_columns = {
        date_column,
        latitude_column,
        longitude_column,
    }

    missing_columns = (
        required_columns
        - set(dataframe.columns)
    )

    if missing_columns:
        raise ValueError(
            "CHIRPS extraction request is missing "
            f"columns: {sorted(missing_columns)}"
        )

    if dataframe[
        date_column
    ].isna().any():
        raise ValueError(
            "CHIRPS extraction request contains "
            "missing dates."
        )

    if dataframe[
        latitude_column
    ].isna().any():
        raise ValueError(
            "CHIRPS extraction request contains "
            "missing latitude values."
        )

    if dataframe[
        longitude_column
    ].isna().any():
        raise ValueError(
            "CHIRPS extraction request contains "
            "missing longitude values."
        )

    if not dataframe[
        latitude_column
    ].between(-90, 90).all():
        raise ValueError(
            "Invalid latitude values detected."
        )

    if not dataframe[
        longitude_column
    ].between(-180, 180).all():
        raise ValueError(
            "Invalid longitude values detected."
        )


# SINGLE-PIXEL SAMPLING

def sample_chirps_value(
    dataset: rasterio.io.DatasetReader,
    band_number: int,
    x: float,
    y: float,
    max_search_radius: int = MAX_FALLBACK_RADIUS,
) -> tuple[float, str, int]:
    """
    Sample one CHIRPS rainfall value.

    The requested raster cell is sampled first.

    If that cell is masked/NoData, the function searches
    outward for the nearest valid CHIRPS cell from the
    SAME date/band.

    This fallback is intended to handle edge effects
    introduced when coarse CHIRPS pixels were masked to
    the exact Nairobi County boundary.

    Returns:
        rainfall_mm
        sampling_method
        fallback_distance_pixels
    """

    row, col = dataset.index(
        x,
        y,
    )

    
    # Direct sample
    
    if (
        0 <= row < dataset.height
        and 0 <= col < dataset.width
    ):
        direct = dataset.read(
            band_number,
            window=(
                (row, row + 1),
                (col, col + 1),
            ),
            masked=True,
        )

        value = direct[0, 0]

        if not np.ma.is_masked(value):
            return (
                float(value),
                "direct",
                0,
            )

    # Nearest-valid-cell fallback
    

    for radius in range(
        1,
        max_search_radius + 1,
    ):

        row_min = max(
            row - radius,
            0,
        )

        row_max = min(
            row + radius,
            dataset.height - 1,
        )

        col_min = max(
            col - radius,
            0,
        )

        col_max = min(
            col + radius,
            dataset.width - 1,
        )

        candidates = []

        for candidate_row in range(
            row_min,
            row_max + 1,
        ):
            for candidate_col in range(
                col_min,
                col_max + 1,
            ):

                # Search only the current outer ring.
                if max(
                    abs(candidate_row - row),
                    abs(candidate_col - col),
                ) != radius:
                    continue

                sample = dataset.read(
                    band_number,
                    window=(
                        (
                            candidate_row,
                            candidate_row + 1,
                        ),
                        (
                            candidate_col,
                            candidate_col + 1,
                        ),
                    ),
                    masked=True,
                )

                value = sample[
                    0,
                    0,
                ]

                if np.ma.is_masked(value):
                    continue

                euclidean_distance = (
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
                        euclidean_distance,
                        float(value),
                    )
                )

        if candidates:
            candidates.sort(
                key=lambda item: item[0]
            )

            nearest_value = (
                candidates[0][1]
            )

            return (
                nearest_value,
                "nearest_valid",
                radius,
            )

    
    # No valid fallback found
    

    return (
        np.nan,
        "missing",
        max_search_radius,
    )


# DAILY RAINFALL EXTRACTION


def extract_daily_rainfall(
    requests: pd.DataFrame,
    date_column: str = "date",
    latitude_column: str = "latitude",
    longitude_column: str = "longitude",
) -> pd.DataFrame:
    """
    Extract CHIRPS daily rainfall for arbitrary
    location/date requests.

    The returned rainfall value is expressed in mm/day.

    Input coordinates are expected in WGS84
    latitude/longitude.
    """

    dataframe = requests.copy()

    validate_location_date_requests(
        dataframe,
        date_column,
        latitude_column,
        longitude_column,
    )

    dataframe[date_column] = (
        pd.to_datetime(
            dataframe[date_column],
            errors="raise",
        )
        .dt.date
    )

    year_files = (
        discover_chirps_files()
    )

    dataframe["_year"] = (
        dataframe[
            date_column
        ].map(
            lambda value: value.year
        )
    )

    unavailable_years = (
        set(
            dataframe["_year"]
        )
        - set(
            year_files.keys()
        )
    )

    if unavailable_years:
        raise ValueError(
            "CHIRPS rainfall requested for "
            "unavailable years: "
            f"{sorted(unavailable_years)}"
        )

    
    # Output columns
    

    dataframe["rainfall_mm"] = np.nan

    dataframe["chirps_year"] = (
        dataframe["_year"]
    )

    dataframe["chirps_band"] = pd.Series(
        pd.NA,
        index=dataframe.index,
        dtype="Int64",
    )

    dataframe[
        "chirps_sampling_method"
    ] = pd.Series(
        None,
        index=dataframe.index,
        dtype="object",
    )

    dataframe[
        "chirps_fallback_distance_pixels"
    ] = pd.Series(
        pd.NA,
        index=dataframe.index,
        dtype="Int64",
    )

    
    # Process each annual raster only once
    

    for year, year_rows in dataframe.groupby(
        "_year"
    ):

        file_path = year_files[
            int(year)
        ]

        with rasterio.open(
            file_path
        ) as dataset:

            if dataset.crs is None:
                raise ValueError(
                    "CHIRPS raster does not define "
                    f"a CRS: {file_path.name}"
                )

            band_dates = get_band_dates(
                dataset,
                int(year),
            )

            band_lookup = {
                band_date:
                    band_number

                for band_number, band_date
                in enumerate(
                    band_dates,
                    start=1,
                )
            }

            requested_dates = set(
                year_rows[
                    date_column
                ]
            )

            missing_dates = (
                requested_dates
                - set(
                    band_lookup.keys()
                )
            )

            if missing_dates:
                raise ValueError(
                    "Requested CHIRPS dates are "
                    f"missing for {year}: "
                    f"{sorted(missing_dates)[:10]}"
                )

            
            # Process one date/band at a time
        

            for (
                requested_date,
                date_rows,
            ) in year_rows.groupby(
                date_column
            ):

                band_number = (
                    band_lookup[
                        requested_date
                    ]
                )

                longitudes = (
                    date_rows[
                        longitude_column
                    ]
                    .astype(float)
                    .tolist()
                )

                latitudes = (
                    date_rows[
                        latitude_column
                    ]
                    .astype(float)
                    .tolist()
                )

                if dataset.crs == WGS84:
                    raster_x = longitudes
                    raster_y = latitudes

                else:
                    (
                        raster_x,
                        raster_y,
                    ) = transform(
                        WGS84,
                        dataset.crs,
                        longitudes,
                        latitudes,
                    )

                coordinates = list(
                    zip(
                        raster_x,
                        raster_y,
                    )
                )

                sampled_values = []
                sampling_methods = []
                fallback_distances = []

                for x, y in coordinates:

                    (
                        value,
                        method,
                        distance_pixels,
                    ) = sample_chirps_value(
                        dataset=dataset,
                        band_number=band_number,
                        x=x,
                        y=y,
                    )

                    sampled_values.append(
                        value
                    )

                    sampling_methods.append(
                        method
                    )

                    fallback_distances.append(
                        distance_pixels
                    )

                dataframe.loc[
                    date_rows.index,
                    "rainfall_mm",
                ] = sampled_values

                dataframe.loc[
                    date_rows.index,
                    "chirps_sampling_method",
                ] = sampling_methods

                dataframe.loc[
                    date_rows.index,
                    "chirps_fallback_distance_pixels",
                ] = fallback_distances

                dataframe.loc[
                    date_rows.index,
                    "chirps_band",
                ] = band_number

    
    # Final quality checks
    

    negative_rainfall = (
        dataframe[
            "rainfall_mm"
        ]
        .dropna()
        < 0
    )

    if negative_rainfall.any():
        raise ValueError(
            "Negative CHIRPS rainfall values "
            "were detected after extraction."
        )

    valid_methods = {
        "direct",
        "nearest_valid",
        "missing",
    }

    unexpected_methods = (
        set(
            dataframe[
                "chirps_sampling_method"
            ]
            .dropna()
            .unique()
        )
        - valid_methods
    )

    if unexpected_methods:
        raise ValueError(
            "Unexpected CHIRPS sampling methods: "
            f"{sorted(unexpected_methods)}"
        )

    return (
        dataframe
        .drop(
            columns=[
                "_year",
            ]
        )
        .reset_index(
            drop=True
        )
    )



# GROUNDSOURCE EVENT-DATE REQUESTS


def load_groundsource_event_requests() -> pd.DataFrame:
    """
    Build a location/date extraction request from the
    Groundsource positive-candidate output.

    This is used as a real-data validation of the CHIRPS
    extraction engine.

    It does NOT construct the final modelling dataset.
    """

    flood_label_file = (
        PROCESSED_DIR
        / "flood_labels"
        / "groundsource_positive_candidates.csv"
    )

    if not flood_label_file.exists():
        raise FileNotFoundError(
            "Processed Groundsource flood labels "
            "were not found. Run Task 7 first: "
            f"{flood_label_file}"
        )

    dataframe = pd.read_csv(
        flood_label_file
    )

    required_columns = {
        "label_record_id",
        "event_group_id",
        "start_date",
        "latitude",
        "longitude",
        "verification_status",
    }

    missing = (
        required_columns
        - set(dataframe.columns)
    )

    if missing:
        raise ValueError(
            "Processed Groundsource file is missing "
            f"columns: {sorted(missing)}"
        )

    requests = dataframe[
        [
            "label_record_id",
            "event_group_id",
            "start_date",
            "latitude",
            "longitude",
            "verification_status",
        ]
    ].copy()

    requests = requests.rename(
        columns={
            "start_date": "date",
        }
    )

    return requests



# OUTPUT WRITER


def save_chirps_outputs(
    inventory: pd.DataFrame,
    rainfall: pd.DataFrame,
) -> None:
    """
    Save CHIRPS archive metadata and extracted
    event-date rainfall observations.
    """

    CHIRPS_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    inventory_file = (
        CHIRPS_OUTPUT_DIR
        / "chirps_archive_inventory.csv"
    )

    rainfall_file = (
        CHIRPS_OUTPUT_DIR
        / "groundsource_event_date_rainfall.csv"
    )

    inventory.to_csv(
        inventory_file,
        index=False,
    )

    rainfall.to_csv(
        rainfall_file,
        index=False,
    )

    print(
        f"Saved CHIRPS inventory: "
        f"{inventory_file}"
    )

    print(
        f"Saved event-date rainfall: "
        f"{rainfall_file}"
    )