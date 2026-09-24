from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
import re
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform
from config import paths as path_config
from config.paths import PROCESSED_DIR


# Paths
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

POSITIVE_GIS_FILE = (
    FEATURES_DIR
    / "environmental"
    / "groundsource_environmental_features.csv"
)

GIS_INVENTORY_FILE = (
    PROCESSED_DIR
    / "environmental"
    / "gis_raster_inventory.csv"
)

MODEL_ENVIRONMENTAL_DIR = (
    FEATURES_DIR
    / "modeling"
    / "environmental"
)

ENVIRONMENTAL_OUTPUT_FILE = (
    MODEL_ENVIRONMENTAL_DIR
    / "historical_model_environmental_features.csv"
)

GHSL_SAMPLING_OUTPUT_FILE = (
    MODEL_ENVIRONMENTAL_DIR
    / "historical_model_ghsl_sampling.csv"
)


# GHSL configuration
GHSL_EPOCHS = (
    2000,
    2005,
    2010,
    2015,
    2020,
    2025,
)

# GHSL built-surface raster represents built-up square metres within a 100 m × 100 m pixel.
GHSL_PIXEL_AREA_M2 = 10_000.0


# Generic helpers
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


def _normalise_identifier(
    series: pd.Series,
) -> pd.Series:

    result = (
        series
        .astype("string")
        .str.strip()
    )

    result = result.replace(
        {
            "":
                pd.NA,

            "nan":
                pd.NA,

            "None":
                pd.NA,

            "<NA>":
                pd.NA,
        }
    )

    return result


# Label loading
def load_model_labels() -> pd.DataFrame:

    labels = pd.read_csv(
        MODEL_LABEL_FILE
    )

    _require_columns(
        labels,
        {
            "model_record_id",
            "source_label_record_id",
            "reference_positive_id",
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
            "Historical model labels "
            "contain duplicate model IDs."
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
        labels[
            "flood_label"
        ].unique()
    ).issubset(
        {0, 1}
    ):

        raise ValueError(
            "flood_label must contain "
            "only 0 and 1."
        )

    labels[
        "source_label_record_id"
    ] = _normalise_identifier(
        labels[
            "source_label_record_id"
        ]
    )

    labels[
        "reference_positive_id"
    ] = _normalise_identifier(
        labels[
            "reference_positive_id"
        ]
    )

    return labels


# Existing positive GIS features
def load_positive_gis_features() -> pd.DataFrame:

    gis = pd.read_csv(
        POSITIVE_GIS_FILE
    )

    _require_columns(
        gis,
        {
            "elevation_m",
            "slope_deg",
            "flood_hazard_score",
            "ghsl_epoch",
            "ghsl_built_surface_m2",
            "ghsl_built_up_fraction",
        },
        "Groundsource environmental features",
    )

    identifier_column = (
        _resolve_column(
            gis,
            [
                "label_record_id",
                "source_label_record_id",
                "record_id",
            ],
            "GIS label identifier",
        )
    )

    gis = gis.rename(
        columns={
            identifier_column:
                "source_label_record_id"
        }
    )

    gis[
        "source_label_record_id"
    ] = _normalise_identifier(
        gis[
            "source_label_record_id"
        ]
    )

    if gis[
        "source_label_record_id"
    ].isna().any():

        raise ValueError(
            "Positive GIS feature dataset "
            "contains missing identifiers."
        )

    if gis[
        "source_label_record_id"
    ].duplicated().any():

        raise ValueError(
            "Positive GIS feature dataset "
            "contains duplicate identifiers."
        )

    return gis


# Static environmental features
def build_static_environmental_features(
    labels: pd.DataFrame,
    positive_gis: pd.DataFrame,
) -> pd.DataFrame:

    positives = (
        labels[
            labels[
                "flood_label"
            ] == 1
        ]
        .copy()
    )

    if positives[
        "source_label_record_id"
    ].isna().any():

        raise ValueError(
            "Positive model observations "
            "contain missing source IDs."
        )

    positive_static = (
        positives[
            [
                "model_record_id",
                "source_label_record_id",
            ]
        ]
        .merge(
            positive_gis[
                [
                    "source_label_record_id",
                    "elevation_m",
                    "slope_deg",
                    "flood_hazard_score",
                ]
            ],
            on="source_label_record_id",
            how="left",
            validate="one_to_one",
        )
    )

    static_columns = [
        "elevation_m",
        "slope_deg",
        "flood_hazard_score",
    ]

    if positive_static[
        static_columns
    ].isna().any().any():

        missing = (
            positive_static[
                static_columns
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
            "Positive static environmental "
            "features contain missing values:\n"
            f"{missing}"
        )

    positive_static = (
        positive_static
        .rename(
            columns={
                "model_record_id":
                    "static_reference_positive_id"
            }
        )
    )

    working = labels.copy()

    working[
        "static_reference_positive_id"
    ] = np.where(
        working[
            "flood_label"
        ] == 1,
        working[
            "model_record_id"
        ],
        working[
            "reference_positive_id"
        ],
    )

    working[
        "static_reference_positive_id"
    ] = _normalise_identifier(
        working[
            "static_reference_positive_id"
        ]
    )

    static_features = (
        working[
            [
                "model_record_id",
                "static_reference_positive_id",
            ]
        ]
        .merge(
            positive_static[
                [
                    "static_reference_positive_id",
                    "elevation_m",
                    "slope_deg",
                    "flood_hazard_score",
                ]
            ],
            on="static_reference_positive_id",
            how="left",
            validate="many_to_one",
        )
    )

    if static_features[
        static_columns
    ].isna().any().any():

        missing = (
            static_features[
                static_columns
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
            "Static environmental feature "
            "mapping failed:\n"
            f"{missing}"
        )

    return static_features


# GHSL raster discovery
def _configured_search_roots() -> list[Path]:

    roots = []

    relevant_names = (
        "GIS",
        "ENV",
        "RAW",
        "DATA",
        "GHSL",
    )

    for name, value in vars(
        path_config
    ).items():

        if not isinstance(
            value,
            Path,
        ):
            continue

        if not any(
            token in name.upper()
            for token in relevant_names
        ):
            continue

        path = value.expanduser()

        if (
            path.exists()
            and path.is_dir()
        ):
            roots.append(
                path.resolve()
            )

    # Project directory is a final fallback.
    roots.append(
        Path.cwd().resolve()
    )

    unique_roots = []

    seen = set()

    for root in roots:

        root_key = str(root)

        if root_key in seen:
            continue

        seen.add(
            root_key
        )

        unique_roots.append(
            root
        )

    return unique_roots


@lru_cache(maxsize=None)
def _resolve_raster_file(
    file_value: str,
) -> Path:

    candidate = Path(
        str(file_value)
    ).expanduser()

    if candidate.exists():

        return candidate.resolve()

    file_name = (
        candidate.name
    )

    roots = (
        _configured_search_roots()
    )

    # First try direct joins because they are much cheaper than rglob.
    for root in roots:

        direct_candidates = [
            root / file_value,
            root / file_name,
            root
            / "environmental"
            / file_name,
            root
            / "gis"
            / file_name,
            root
            / "ghsl"
            / file_name,
        ]

        for direct in (
            direct_candidates
        ):

            if direct.exists():

                return direct.resolve()

    # Fall back to recursive search.
    matches = []

    for root in roots:

        try:

            root_matches = list(
                root.rglob(
                    file_name
                )
            )

        except (
            OSError,
            PermissionError,
        ):
            continue

        for match in (
            root_matches
        ):

            if match.is_file():

                matches.append(
                    match.resolve()
                )

    unique_matches = list(
        dict.fromkeys(
            matches
        )
    )

    if not unique_matches:

        raise FileNotFoundError(
            "Could not resolve GIS raster "
            f"from inventory value: "
            f"{file_value}"
        )

    # If duplicate copies exist, use the shortest resolved path deterministically.
    unique_matches.sort(
        key=lambda path:
        (
            len(
                str(path)
            ),
            str(path),
        )
    )

    return unique_matches[0]


def _extract_epoch(
    text: str,
) -> int | None:

    match = re.search(
        r"(?<!\d)"
        r"(2000|2005|2010|2015|2020|2025)"
        r"(?!\d)",
        text,
    )

    if match is None:

        return None

    return int(
        match.group(1)
    )


def discover_ghsl_rasters() -> dict[
    int,
    Path,
]:

    inventory = pd.read_csv(
        GIS_INVENTORY_FILE
    )

    if inventory.empty:

        raise ValueError(
            "GIS raster inventory "
            "is empty."
        )

    raster_map = {}

    for _, row in (
        inventory.iterrows()
    ):

        row_values = [
            str(value)
            for value
            in row.tolist()
            if pd.notna(value)
        ]

        row_text = (
            " ".join(
                row_values
            )
        )

        row_text_lower = (
            row_text.lower()
        )

        if (
            "ghsl"
            not in row_text_lower
            and "ghs_built"
            not in row_text_lower
        ):
            continue

        epoch = (
            _extract_epoch(
                row_text
            )
        )

        if epoch is None:
            continue

        raster_values = [
            value
            for value
            in row_values
            if value.lower().endswith(
                (
                    ".tif",
                    ".tiff",
                )
            )
        ]

        if not raster_values:

            continue

        resolved_path = None

        for value in raster_values:

            try:

                resolved_path = (
                    _resolve_raster_file(
                        value
                    )
                )

                break

            except FileNotFoundError:
                continue

        if resolved_path is None:
            continue

        if (
            epoch in raster_map
            and raster_map[
                epoch
            ] != resolved_path
        ):

            raise ValueError(
                "Multiple different GHSL "
                f"rasters resolved for "
                f"epoch {epoch}: "
                f"{raster_map[epoch]} and "
                f"{resolved_path}"
            )

        raster_map[
            epoch
        ] = resolved_path

    missing_epochs = (
        set(
            GHSL_EPOCHS
        )
        - set(
            raster_map
        )
    )

    if missing_epochs:

        raise FileNotFoundError(
            "Could not resolve GHSL "
            "rasters for epochs: "
            f"{sorted(missing_epochs)}"
        )

    return raster_map


# GHSL sampling
def select_ghsl_epoch(
    observation_year: int,
) -> int:

    valid_epochs = [
        epoch
        for epoch
        in GHSL_EPOCHS
        if epoch
        <= observation_year
    ]

    if not valid_epochs:

        raise ValueError(
            "No historical GHSL epoch "
            "exists for observation year "
            f"{observation_year}."
        )

    return max(
        valid_epochs
    )


def _coordinates_for_dataset(
    dataset,
    longitude: float,
    latitude: float,
) -> tuple[
    float,
    float,
]:

    if (
        dataset.crs is None
        or str(
            dataset.crs
        ).upper()
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


def _sample_ghsl_value(
    dataset,
    longitude: float,
    latitude: float,
) -> float:

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
        or row_index
        >= dataset.height
        or col_index < 0
        or col_index
        >= dataset.width
    ):

        raise ValueError(
            "GHSL request lies outside "
            "the raster extent."
        )

    value = dataset.read(
        1,
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

    if not np.isfinite(
        value
    ):

        raise ValueError(
            "GHSL returned a "
            "non-finite value."
        )

    nodata = (
        dataset.nodata
    )

    if (
        nodata is not None
        and np.isfinite(
            nodata
        )
        and np.isclose(
            value,
            nodata,
        )
    ):

        raise ValueError(
            "GHSL returned nodata "
            "for a modelling location."
        )

    value = float(
        value
    )

    if value < 0:

        raise ValueError(
            "GHSL built-surface value "
            f"is negative: {value}"
        )

    if (
        value
        > GHSL_PIXEL_AREA_M2
        + 1.0
    ):

        raise ValueError(
            "GHSL built-surface value "
            "exceeds expected 100 m "
            f"pixel area: {value}"
        )

    return value


def build_ghsl_features(labels: pd.DataFrame,) -> pd.DataFrame:
    raster_map = (
        discover_ghsl_rasters()
    )

    sampled_rows = []

    with ExitStack() as stack:

        datasets = {
            epoch:
                stack.enter_context(
                    rasterio.open(
                        path
                    )
                )
            for epoch, path
            in raster_map.items()
        }

        for row in labels.itertuples(index=False):
            observation_date = (
                pd.Timestamp(
                    row.observation_date
                )
            )

            epoch = (
                select_ghsl_epoch(
                    observation_date.year
                )
            )

            dataset = (
                datasets[
                    epoch
                ]
            )

            built_surface = (
                _sample_ghsl_value(
                    dataset,
                    float(
                        row.longitude
                    ),
                    float(
                        row.latitude
                    ),
                )
            )

            built_fraction = (built_surface/ GHSL_PIXEL_AREA_M2)

            sampled_rows.append(
                {
                    "model_record_id":
                        row.model_record_id,

                    "ghsl_epoch":
                        epoch,

                    "ghsl_built_surface_m2":
                        built_surface,

                    "ghsl_built_up_fraction":
                        built_fraction,

                    "ghsl_sampling_method":
                        "direct",
                }
            )

    result = pd.DataFrame(sampled_rows)

    if len(result) != len(labels):
        raise ValueError("GHSL feature row count does not match model labels.")

    if result["model_record_id"].duplicated().any():
        raise ValueError("GHSL output contains duplicate model IDs.")

    return result


# Consistency check 
def validate_positive_ghsl_consistency(labels: pd.DataFrame, positive_gis: pd.DataFrame, ghsl_features: pd.DataFrame,) -> None:
    positives = (
        labels[
            labels[
                "flood_label"
            ] == 1
        ][
            [
                "model_record_id",
                "source_label_record_id",
            ]
        ]
        .copy()
    )

    existing = (
        positives
        .merge(
            positive_gis[
                [
                    "source_label_record_id",
                    "ghsl_epoch",
                    "ghsl_built_surface_m2",
                    "ghsl_built_up_fraction",
                ]
            ],
            on="source_label_record_id",
            how="left",
            validate="one_to_one",
        )
        .rename(
            columns={
                "ghsl_epoch":
                    "existing_epoch",

                "ghsl_built_surface_m2":
                    "existing_surface",

                "ghsl_built_up_fraction":
                    "existing_fraction",
            }
        )
    )

    comparison = (
        existing
        .merge(
            ghsl_features[
                [
                    "model_record_id",
                    "ghsl_epoch",
                    "ghsl_built_surface_m2",
                    "ghsl_built_up_fraction",
                ]
            ],
            on="model_record_id",
            how="left",
            validate="one_to_one",
        )
    )

    if comparison[
        [
            "existing_epoch",
            "existing_surface",
            "existing_fraction",
            "ghsl_epoch",
            "ghsl_built_surface_m2",
            "ghsl_built_up_fraction",
        ]
    ].isna().any().any():

        raise ValueError("Unable to validate positive GHSL consistency because values are missing.")

    epoch_match = (
        comparison[
            "existing_epoch"
        ].astype(int)
        == comparison[
            "ghsl_epoch"
        ].astype(int)
    )

    surface_match = np.isclose(
        comparison["existing_surface"],
        comparison["ghsl_built_surface_m2"],
        rtol=1e-6,
        atol=1e-3,
    )

    fraction_match = np.isclose(
        comparison["existing_fraction"],
        comparison["ghsl_built_up_fraction"],
        rtol=1e-6,
        atol=1e-4,
    )

    if not (
        epoch_match
        & surface_match
        & fraction_match
    ).all():

        mismatch_count = int(
            (
                ~(
                    epoch_match
                    & surface_match
                    & fraction_match
                )
            ).sum())

        raise ValueError(f"New GHSL extraction does not reproduce Task 12 positive features for {mismatch_count:,} rows.")


# Final environmental dataset
def build_model_environmental_features():

    labels = (load_model_labels())
    positive_gis = (load_positive_gis_features())
    static_features = (build_static_environmental_features(labels, positive_gis,))
    ghsl_features = (build_ghsl_features(labels))
    validate_positive_ghsl_consistency(labels, positive_gis, ghsl_features,)
    combined = (
        labels[
            [
                "model_record_id",
                "flood_label",
                "observation_date",
                "latitude",
                "longitude",
            ]
        ]
        .merge(
            static_features,
            on="model_record_id",
            how="left",
            validate="one_to_one",
        )
        .merge(
            ghsl_features,
            on="model_record_id",
            how="left",
            validate="one_to_one",
        )
    )

    feature_columns = [
        "elevation_m",
        "slope_deg",
        "flood_hazard_score",
        "ghsl_built_surface_m2",
        "ghsl_built_up_fraction",
    ]

    if len(combined) != len(labels):
        raise ValueError("Environmental feature row count does not match model labels.")

    if combined["model_record_id"].duplicated().any():
        raise ValueError("Environmental model dataset contains duplicate model IDs.")

    if combined[feature_columns].isna().any().any():
        missing = (combined[feature_columns].isna().sum())
        missing = (missing[missing > 0])

        raise ValueError(f"Environmental model features contain missing values:\n {missing}")

    if (combined["ghsl_built_up_fraction"] < 0).any():
        raise ValueError("GHSL built-up fraction contains negative values.")

    if (combined["ghsl_built_up_fraction"] > 1.0001).any():
        raise ValueError("GHSL built-up fraction contains values above 1.")

    MODEL_ENVIRONMENTAL_DIR.mkdir(parents=True, exist_ok=True,)

    ghsl_features.to_csv(GHSL_SAMPLING_OUTPUT_FILE, index=False, )

    combined.to_csv(ENVIRONMENTAL_OUTPUT_FILE, index=False, date_format="%Y-%m-%d",)

    return (
        labels,
        static_features,
        ghsl_features,
        combined,
    )