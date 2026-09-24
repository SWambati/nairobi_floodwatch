from contextlib import ExitStack
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform

from config.paths import CHIRPS_DIR, PROCESSED_DIR


# ============================================================
# Paths
# ============================================================

FEATURES_DIR = (
    PROCESSED_DIR.parent
    / "features"
)

MODEL_LABEL_FILE = (
    PROCESSED_DIR
    / "modeling"
    / "labels"
    / "historical_model_labels.csv"
)

ERA5_CANONICAL_FILE = (
    PROCESSED_DIR
    / "weather"
    / "era5_land"
    / "era5_land_hourly_canonical.parquet"
)

MODEL_WEATHER_DIR = (
    FEATURES_DIR
    / "modeling"
    / "weather"
)

CHIRPS_OUTPUT_FILE = (
    MODEL_WEATHER_DIR
    / "historical_model_chirps_features.csv"
)

ERA5_OUTPUT_FILE = (
    MODEL_WEATHER_DIR
    / "historical_model_era5_features.csv"
)

WEATHER_OUTPUT_FILE = (
    MODEL_WEATHER_DIR
    / "historical_model_weather_features.csv"
)


NAIROBI_TIMEZONE = ZoneInfo(
    "Africa/Nairobi"
)

MAX_CHIRPS_FALLBACK_RADIUS = 3


# ============================================================
# Generic helpers
# ============================================================

def _require_columns(
    dataframe: pd.DataFrame,
    required_columns: set[str],
    dataset_name: str,
) -> None:

    missing = (
        required_columns
        - set(dataframe.columns)
    )

    if missing:

        raise ValueError(
            f"{dataset_name} is missing "
            f"required columns: "
            f"{sorted(missing)}"
        )


def load_model_labels() -> pd.DataFrame:

    labels = pd.read_csv(
        MODEL_LABEL_FILE
    )

    _require_columns(
        labels,
        {
            "model_record_id",
            "observation_date",
            "latitude",
            "longitude",
            "flood_label",
        },
        "Historical model labels",
    )

    labels[
        "observation_date"
    ] = pd.to_datetime(
        labels[
            "observation_date"
        ],
        errors="coerce",
    ).dt.normalize()

    if labels[
        "observation_date"
    ].isna().any():

        raise ValueError(
            "Historical model labels "
            "contain invalid dates."
        )

    if labels[
        "model_record_id"
    ].duplicated().any():

        raise ValueError(
            "model_record_id must be unique."
        )

    if labels[
        [
            "latitude",
            "longitude",
        ]
    ].isna().any().any():

        raise ValueError(
            "Historical model labels "
            "contain missing coordinates."
        )

    if not set(
        labels["flood_label"].unique()
    ).issubset(
        {0, 1}
    ):

        raise ValueError(
            "flood_label must contain "
            "only 0 and 1."
        )

    return labels


# ============================================================
# CHIRPS
# ============================================================

def load_chirps_archive() -> dict[int, Path]:

    raster_files = sorted(
        list(
            CHIRPS_DIR.glob(
                "chirps_v3_sat_nairobi_*.tif"
            )
        )
        + list(
            CHIRPS_DIR.glob(
                "chirps_v3_sat_nairobi_*.tiff"
            )
        )
    )

    if not raster_files:

        raise FileNotFoundError(
            "No CHIRPS annual rasters "
            f"found in {CHIRPS_DIR}"
        )

    archive = {}

    for path in raster_files:

        try:

            year = int(
                path.stem.rsplit(
                    "_",
                    1,
                )[-1]
            )

        except ValueError as exc:

            raise ValueError(
                "Could not determine year "
                "from CHIRPS raster filename: "
                f"{path.name}"
            ) from exc

        if year in archive:

            raise ValueError(
                "Duplicate CHIRPS annual "
                f"raster found for year {year}: "
                f"{archive[year].name} and "
                f"{path.name}"
            )

        archive[year] = path

    return archive


def _valid_rainfall_value(
    value: float,
    nodata,
) -> bool:

    if not np.isfinite(value):
        return False

    if (
        nodata is not None
        and np.isfinite(nodata)
        and np.isclose(
            value,
            nodata,
        )
    ):
        return False

    # CHIRPS rainfall must not be negative.
    if value < 0:
        return False

    return True


def _coordinates_for_dataset(
    dataset,
    longitude: float,
    latitude: float,
) -> tuple[float, float]:

    if (
        dataset.crs is None
        or str(dataset.crs).upper()
        in {
            "EPSG:4326",
            "OGC:CRS84",
        }
    ):

        return (
            longitude,
            latitude,
        )

    xs, ys = transform(
        "EPSG:4326",
        dataset.crs,
        [longitude],
        [latitude],
    )

    return (
        xs[0],
        ys[0],
    )


def _sample_chirps_value(
    dataset,
    band_index: int,
    longitude: float,
    latitude: float,
) -> tuple[
    float,
    str,
    int,
]:

    x, y = (
        _coordinates_for_dataset(
            dataset,
            longitude,
            latitude,
        )
    )

    row_index, col_index = (
        dataset.index(
            x,
            y,
        )
    )

    if (
        row_index < 0
        or row_index >= dataset.height
        or col_index < 0
        or col_index >= dataset.width
    ):

        raise ValueError(
            "CHIRPS request lies outside "
            "the raster extent."
        )

    direct = dataset.read(
        band_index,
        window=(
            (
                row_index,
                row_index + 1,
            ),
            (
                col_index,
                col_index + 1,
            ),
        ),
    )[0, 0]

    if _valid_rainfall_value(
        direct,
        dataset.nodata,
    ):

        return (
            float(direct),
            "direct",
            0,
        )

    # Search neighbouring pixels when the
    # requested CHIRPS pixel contains nodata.
    for radius in range(
        1,
        MAX_CHIRPS_FALLBACK_RADIUS + 1,
    ):

        row_start = max(
            0,
            row_index - radius,
        )

        row_end = min(
            dataset.height,
            row_index + radius + 1,
        )

        col_start = max(
            0,
            col_index - radius,
        )

        col_end = min(
            dataset.width,
            col_index + radius + 1,
        )

        window = dataset.read(
            band_index,
            window=(
                (
                    row_start,
                    row_end,
                ),
                (
                    col_start,
                    col_end,
                ),
            ),
        )

        candidates = []

        for local_row in range(
            window.shape[0]
        ):

            for local_col in range(
                window.shape[1]
            ):

                value = window[
                    local_row,
                    local_col,
                ]

                if not _valid_rainfall_value(
                    value,
                    dataset.nodata,
                ):
                    continue

                absolute_row = (
                    row_start
                    + local_row
                )

                absolute_col = (
                    col_start
                    + local_col
                )

                distance = np.hypot(
                    absolute_row
                    - row_index,
                    absolute_col
                    - col_index,
                )

                candidates.append(
                    (
                        distance,
                        float(value),
                    )
                )

        if candidates:

            candidates.sort(
                key=lambda item:
                item[0]
            )

            return (
                candidates[0][1],
                "nearest_valid",
                radius,
            )

    return (
        np.nan,
        "missing",
        MAX_CHIRPS_FALLBACK_RADIUS,
    )


def build_chirps_model_features(
    labels: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:

    archive = (
        load_chirps_archive()
    )

    requests = []

    for row in labels.itertuples(
        index=False
    ):

        observation_date = (
            pd.Timestamp(
                row.observation_date
            )
        )

        for lag in range(
            1,
            8,
        ):

            request_date = (
                observation_date
                - pd.Timedelta(
                    days=lag
                )
            )

            requests.append(
                {
                    "model_record_id":
                        row.model_record_id,

                    "request_date":
                        request_date,

                    "lag_day":
                        lag,

                    "latitude":
                        float(
                            row.latitude
                        ),

                    "longitude":
                        float(
                            row.longitude
                        ),
                }
            )

    request_df = pd.DataFrame(
        requests
    )

    sampled_rows = []

    with ExitStack() as stack:

        datasets = {}

        for year, path in (
            archive.items()
        ):

            datasets[year] = (
                stack.enter_context(
                    rasterio.open(
                        path
                    )
                )
            )

        for row in (
            request_df.itertuples(
                index=False
            )
        ):

            request_date = (
                pd.Timestamp(
                    row.request_date
                )
            )

            year = (
                request_date.year
            )

            if year not in datasets:

                raise ValueError(
                    "Missing CHIRPS raster "
                    f"for year {year}."
                )

            dataset = (
                datasets[year]
            )

            band_index = int(
                request_date.dayofyear
            )

            if band_index > dataset.count:

                raise ValueError(
                    "CHIRPS raster has "
                    f"{dataset.count} bands, "
                    f"but date "
                    f"{request_date.date()} "
                    f"requires band "
                    f"{band_index}."
                )

            (
                rainfall_mm,
                sampling_method,
                fallback_radius,
            ) = _sample_chirps_value(
                dataset,
                band_index,
                row.longitude,
                row.latitude,
            )

            sampled_rows.append(
                {
                    "model_record_id":
                        row.model_record_id,

                    "request_date":
                        request_date,

                    "lag_day":
                        row.lag_day,

                    "rainfall_mm":
                        rainfall_mm,

                    "sampling_method":
                        sampling_method,

                    "fallback_radius_pixels":
                        fallback_radius,
                }
            )

    daily = pd.DataFrame(
        sampled_rows
    )

    expected_requests = (
        len(labels)
        * 7
    )

    if len(daily) != expected_requests:

        raise ValueError(
            "Unexpected CHIRPS request count. "
            f"Expected {expected_requests:,}, "
            f"received {len(daily):,}."
        )

    if daily[
        "rainfall_mm"
    ].isna().any():

        missing = int(
            daily[
                "rainfall_mm"
            ].isna().sum()
        )

        raise ValueError(
            f"{missing:,} CHIRPS requests "
            "could not be resolved."
        )

    counts = (
        daily
        .groupby(
            "model_record_id"
        )
        .size()
    )

    if not (
        counts == 7
    ).all():

        raise ValueError(
            "Each modelling observation "
            "must have exactly seven "
            "CHIRPS antecedent days."
        )

    rainfall_wide = (
        daily.pivot(
            index="model_record_id",
            columns="lag_day",
            values="rainfall_mm",
        )
    )

    result = pd.DataFrame(
        {
            "model_record_id":
                rainfall_wide.index,

            "rain_1d":
                rainfall_wide[1],

            "rain_3d":
                rainfall_wide[
                    [
                        1,
                        2,
                        3,
                    ]
                ].sum(
                    axis=1
                ),

            "rain_7d":
                rainfall_wide[
                    [
                        1,
                        2,
                        3,
                        4,
                        5,
                        6,
                        7,
                    ]
                ].sum(
                    axis=1
                ),
        }
    ).reset_index(
        drop=True
    )

    if len(result) != len(labels):

        raise ValueError(
            "CHIRPS feature row count "
            "does not match model labels."
        )

    if not (
        (
            result["rain_1d"]
            <= result["rain_3d"]
        )
        & (
            result["rain_3d"]
            <= result["rain_7d"]
        )
    ).all():

        raise ValueError(
            "CHIRPS rainfall consistency "
            "check failed."
        )

    return (
        result,
        daily,
    )


# ============================================================
# ERA5-Land
# ============================================================

def _resolve_column(
    dataframe: pd.DataFrame,
    candidates: list[str],
    description: str,
) -> str:

    for candidate in candidates:

        if candidate in dataframe.columns:
            return candidate

    raise ValueError(
        f"Unable to identify "
        f"{description}. "
        f"Tried: {candidates}"
    )


def _haversine_km(
    latitude_1,
    longitude_1,
    latitude_2,
    longitude_2,
):

    earth_radius_km = (
        6371.0088
    )

    lat1 = np.radians(
        latitude_1
    )

    lon1 = np.radians(
        longitude_1
    )

    lat2 = np.radians(
        latitude_2
    )

    lon2 = np.radians(
        longitude_2
    )

    delta_lat = (
        lat2 - lat1
    )

    delta_lon = (
        lon2 - lon1
    )

    a = (
        np.sin(
            delta_lat / 2
        ) ** 2
        + np.cos(lat1)
        * np.cos(lat2)
        * np.sin(
            delta_lon / 2
        ) ** 2
    )

    return (
        2
        * earth_radius_km
        * np.arcsin(
            np.sqrt(a)
        )
    )


def build_era5_model_features(
    labels: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:

    era5 = pd.read_parquet(
        ERA5_CANONICAL_FILE
    )

    timestamp_column = (
        _resolve_column(
            era5,
            [
                "timestamp",
                "valid_time",
                "time",
                "datetime",
            ],
            "ERA5 timestamp column",
        )
    )

    latitude_column = (
        _resolve_column(
            era5,
            [
                "latitude",
                "lat",
            ],
            "ERA5 latitude column",
        )
    )

    longitude_column = (
        _resolve_column(
            era5,
            [
                "longitude",
                "lon",
            ],
            "ERA5 longitude column",
        )
    )

    _require_columns(
        era5,
        {
            "d2m",
            "t2m",
            "sp",
            "tp",
            "swvl1",
            "swvl2",
        },
        "ERA5 canonical archive",
    )

    era5[
        timestamp_column
    ] = pd.to_datetime(
        era5[
            timestamp_column
        ],
        utc=True,
        errors="coerce",
    )

    if era5[
        timestamp_column
    ].isna().any():

        raise ValueError(
            "ERA5 contains invalid "
            "timestamps."
        )

    grid_points = (
        era5[
            [
                latitude_column,
                longitude_column,
            ]
        ]
        .drop_duplicates()
        .reset_index(
            drop=True
        )
    )

    if grid_points.empty:

        raise ValueError(
            "ERA5 archive contains no "
            "grid coordinates."
        )

    mappings = []

    for row in labels.itertuples(
        index=False
    ):

        distances = (
            _haversine_km(
                float(
                    row.latitude
                ),
                float(
                    row.longitude
                ),
                grid_points[
                    latitude_column
                ].to_numpy(),
                grid_points[
                    longitude_column
                ].to_numpy(),
            )
        )

        nearest_index = int(
            np.argmin(
                distances
            )
        )

        grid_row = (
            grid_points.iloc[
                nearest_index
            ]
        )

        mappings.append(
            {
                "model_record_id":
                    row.model_record_id,

                "era5_latitude":
                    float(
                        grid_row[
                            latitude_column
                        ]
                    ),

                "era5_longitude":
                    float(
                        grid_row[
                            longitude_column
                        ]
                    ),

                "era5_grid_distance_km":
                    float(
                        distances[
                            nearest_index
                        ]
                    ),

                "target_local_date":
                    (
                        pd.Timestamp(
                            row.observation_date
                        )
                        - pd.Timedelta(
                            days=1
                        )
                    ),
            }
        )

    mapping_df = pd.DataFrame(
        mappings
    )

    era5 = era5.rename(
        columns={
            latitude_column:
                "era5_latitude",

            longitude_column:
                "era5_longitude",
        }
    )

    era5[
        "local_date"
    ] = (
        era5[
            timestamp_column
        ]
        .dt
        .tz_convert(
            NAIROBI_TIMEZONE
        )
        .dt
        .tz_localize(None)
        .dt
        .normalize()
    )

    hourly = mapping_df.merge(
        era5,
        left_on=[
            "era5_latitude",
            "era5_longitude",
            "target_local_date",
        ],
        right_on=[
            "era5_latitude",
            "era5_longitude",
            "local_date",
        ],
        how="left",
        validate="many_to_many",
    )

    hourly_counts = (
        hourly
        .groupby(
            "model_record_id"
        )
        .size()
    )

    bad_counts = (
        hourly_counts[
            hourly_counts != 24
        ]
    )

    if not bad_counts.empty:

        sample_bad_ids = (
            bad_counts
            .head()
            .to_dict()
        )

        raise ValueError(
            "ERA5 antecedent windows "
            "must contain exactly 24 "
            "hourly rows per observation. "
            f"Invalid observations: "
            f"{len(bad_counts):,}. "
            f"Examples: {sample_bad_ids}"
        )

    expected_hourly_rows = (
        len(labels)
        * 24
    )

    if len(hourly) != expected_hourly_rows:

        raise ValueError(
            "Unexpected ERA5 hourly row "
            f"count. Expected "
            f"{expected_hourly_rows:,}, "
            f"received {len(hourly):,}."
        )

    required_era5_values = [
        "d2m",
        "t2m",
        "sp",
        "tp",
        "swvl1",
        "swvl2",
    ]

    if hourly[
        required_era5_values
    ].isna().any().any():

        missing = (
            hourly[
                required_era5_values
            ]
            .isna()
            .sum()
        )

        missing = (
            missing[
                missing > 0
            ]
        )

        raise ValueError(
            "ERA5 antecedent data "
            "contains missing values:\n"
            f"{missing}"
        )

    hourly[
        "temperature_c"
    ] = (
        hourly["t2m"]
        - 273.15
    )

    hourly[
        "dewpoint_c"
    ] = (
        hourly["d2m"]
        - 273.15
    )

    # Magnus approximation for
    # relative humidity.
    a = 17.625
    b = 243.04

    saturation_dewpoint = (
        np.exp(
            (
                a
                * hourly[
                    "dewpoint_c"
                ]
            )
            /
            (
                b
                + hourly[
                    "dewpoint_c"
                ]
            )
        )
    )

    saturation_temperature = (
        np.exp(
            (
                a
                * hourly[
                    "temperature_c"
                ]
            )
            /
            (
                b
                + hourly[
                    "temperature_c"
                ]
            )
        )
    )

    hourly[
        "relative_humidity_pct"
    ] = (
        100
        * saturation_dewpoint
        / saturation_temperature
    ).clip(
        lower=0,
        upper=100,
    )

    hourly[
        "pressure_hpa"
    ] = (
        hourly["sp"]
        / 100.0
    )

    hourly[
        "precipitation_mm"
    ] = (
        hourly["tp"]
        * 1000.0
    )

    materially_negative = (
        hourly[
            "precipitation_mm"
        ] < -0.01
    )

    if materially_negative.any():

        raise ValueError(
            "ERA5 contains materially "
            "negative precipitation values."
        )

    hourly[
        "precipitation_mm"
    ] = hourly[
        "precipitation_mm"
    ].clip(
        lower=0
    )

    grouped = (
        hourly
        .groupby(
            "model_record_id",
            as_index=False,
        )
        .agg(
            temperature_mean_c=(
                "temperature_c",
                "mean",
            ),

            temperature_min_c=(
                "temperature_c",
                "min",
            ),

            temperature_max_c=(
                "temperature_c",
                "max",
            ),

            relative_humidity_mean_pct=(
                "relative_humidity_pct",
                "mean",
            ),

            pressure_mean_hpa=(
                "pressure_hpa",
                "mean",
            ),

            era5_precip_1d_mm=(
                "precipitation_mm",
                "sum",
            ),

            soil_moisture_layer1_mean=(
                "swvl1",
                "mean",
            ),

            soil_moisture_layer2_mean=(
                "swvl2",
                "mean",
            ),

            era5_grid_distance_km=(
                "era5_grid_distance_km",
                "first",
            ),
        )
    )

    if len(grouped) != len(labels):

        raise ValueError(
            "ERA5 feature row count "
            "does not match model labels."
        )

    return (
        grouped,
        hourly,
    )


# ============================================================
# Combined weather feature dataset
# ============================================================

def build_model_weather_features():

    labels = (
        load_model_labels()
    )

    (
        chirps_features,
        chirps_daily,
    ) = (
        build_chirps_model_features(
            labels
        )
    )

    (
        era5_features,
        era5_hourly,
    ) = (
        build_era5_model_features(
            labels
        )
    )

    combined = (
        labels[
            [
                "model_record_id",
                "flood_label",
                "observation_date",
            ]
        ]
        .merge(
            chirps_features,
            on="model_record_id",
            how="left",
            validate="one_to_one",
        )
        .merge(
            era5_features,
            on="model_record_id",
            how="left",
            validate="one_to_one",
        )
    )

    feature_columns = [
        "rain_1d",
        "rain_3d",
        "rain_7d",
        "temperature_mean_c",
        "temperature_min_c",
        "temperature_max_c",
        "relative_humidity_mean_pct",
        "pressure_mean_hpa",
        "era5_precip_1d_mm",
        "soil_moisture_layer1_mean",
        "soil_moisture_layer2_mean",
    ]

    if len(combined) != len(labels):

        raise ValueError(
            "Weather feature row count "
            "does not match model labels."
        )

    if combined[
        "model_record_id"
    ].duplicated().any():

        raise ValueError(
            "Combined weather dataset "
            "contains duplicate model IDs."
        )

    if combined[
        feature_columns
    ].isna().any().any():

        missing = (
            combined[
                feature_columns
            ]
            .isna()
            .sum()
        )

        missing = (
            missing[
                missing > 0
            ]
        )

        raise ValueError(
            "Weather features contain "
            f"missing values:\n{missing}"
        )

    MODEL_WEATHER_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    chirps_features.to_csv(
        CHIRPS_OUTPUT_FILE,
        index=False,
    )

    era5_features.to_csv(
        ERA5_OUTPUT_FILE,
        index=False,
    )

    combined.to_csv(
        WEATHER_OUTPUT_FILE,
        index=False,
        date_format="%Y-%m-%d",
    )

    return (
        labels,
        chirps_features,
        era5_features,
        combined,
        chirps_daily,
        era5_hourly,
    )