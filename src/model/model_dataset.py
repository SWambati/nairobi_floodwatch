from pathlib import Path

import numpy as np
import pandas as pd

from config.paths import PROCESSED_DIR


# ============================================================
# Paths
# ============================================================

FEATURES_DIR = (
    PROCESSED_DIR.parent
    / "features"
)

LABEL_FILE = (
    PROCESSED_DIR
    / "modeling"
    / "labels"
    / "historical_model_labels.csv"
)

WEATHER_FILE = (
    FEATURES_DIR
    / "modeling"
    / "weather"
    / "historical_model_weather_features.csv"
)

ENVIRONMENTAL_FILE = (
    FEATURES_DIR
    / "modeling"
    / "environmental"
    / "historical_model_environmental_features.csv"
)

MODEL_DATASET_DIR = (
    FEATURES_DIR
    / "modeling"
    / "dataset"
)

MODEL_REPORT_DIR = (
    PROCESSED_DIR
    / "modeling"
    / "dataset"
)

FULL_DATASET_FILE = (
    MODEL_DATASET_DIR
    / "historical_model_dataset.csv"
)

FEATURE_MATRIX_FILE = (
    MODEL_DATASET_DIR
    / "historical_model_feature_matrix.csv"
)

FEATURE_MANIFEST_FILE = (
    MODEL_DATASET_DIR
    / "model_feature_manifest.csv"
)

VALIDATION_REPORT_FILE = (
    MODEL_REPORT_DIR
    / "model_dataset_validation_report.csv"
)


# ============================================================
# Model predictor definitions
# ============================================================

WEATHER_PREDICTORS = [
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


ENVIRONMENTAL_PREDICTORS = [
    "elevation_m",
    "slope_deg",
    "flood_hazard_score",
    "ghsl_built_up_fraction",
]


PREDICTOR_COLUMNS = (
    WEATHER_PREDICTORS
    + ENVIRONMENTAL_PREDICTORS
)


# Columns kept for reproducibility,
# splitting and interpretation, but
# never passed into RF/XGBoost.
METADATA_COLUMNS = [
    "model_record_id",
    "source_label_record_id",
    "source_label_record_ids",
    "source_report_count",
    "event_group_id",
    "split_group_id",
    "observation_date",
    "start_date",
    "end_date",
    "anchor_date",
    "latitude",
    "longitude",
    "location_text",
    "verification_status",
    "label_source",
    "source_name",
    "source_event_id",
    "confidence",
    "primary_source",
    "supporting_source",
    "evidence_class",
    "record_role",
    "weather_join_ready",
    "negative_sampling_strategy",
    "reference_positive_id",
    "reference_positive_event_group_id",
    "year_distance_from_reference",
]


PROVENANCE_COLUMNS = [
    "era5_grid_distance_km",
    "static_reference_positive_id",
    "ghsl_epoch",
    "ghsl_built_surface_m2",
    "ghsl_sampling_method",
]


DATE_COLUMNS = [
    "observation_date",
    "start_date",
    "end_date",
    "anchor_date",
]


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


def _normalise_dates(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:

    result = dataframe.copy()

    for column in DATE_COLUMNS:

        if column not in result.columns:
            continue

        result[column] = pd.to_datetime(
            result[column],
            errors="coerce",
        ).dt.normalize()

    return result


# ============================================================
# Source loading
# ============================================================

def load_sources() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:

    labels = pd.read_csv(
        LABEL_FILE
    )

    weather = pd.read_csv(
        WEATHER_FILE
    )

    environmental = pd.read_csv(
        ENVIRONMENTAL_FILE
    )

    labels = _normalise_dates(
        labels
    )

    weather[
        "observation_date"
    ] = pd.to_datetime(
        weather[
            "observation_date"
        ],
        errors="coerce",
    ).dt.normalize()

    environmental[
        "observation_date"
    ] = pd.to_datetime(
        environmental[
            "observation_date"
        ],
        errors="coerce",
    ).dt.normalize()

    _require_columns(
        labels,
        {
            "model_record_id",
            "observation_date",
            "flood_label",
            "split_group_id",
            "latitude",
            "longitude",
        },
        "Historical model labels",
    )

    _require_columns(
        weather,
        {
            "model_record_id",
            "observation_date",
            "flood_label",
            "era5_grid_distance_km",
            *WEATHER_PREDICTORS,
        },
        "Historical model weather features",
    )

    _require_columns(
        environmental,
        {
            "model_record_id",
            "observation_date",
            "flood_label",
            "static_reference_positive_id",
            "ghsl_epoch",
            "ghsl_built_surface_m2",
            "ghsl_sampling_method",
            *ENVIRONMENTAL_PREDICTORS,
        },
        "Historical model environmental features",
    )

    return (
        labels,
        weather,
        environmental,
    )


# ============================================================
# Alignment validation
# ============================================================

def validate_source_alignment(
    labels: pd.DataFrame,
    source: pd.DataFrame,
    source_name: str,
) -> None:

    if source[
        "model_record_id"
    ].duplicated().any():

        raise ValueError(
            f"{source_name} contains "
            "duplicate model_record_id values."
        )

    comparison = (
        labels[
            [
                "model_record_id",
                "observation_date",
                "flood_label",
            ]
        ]
        .merge(
            source[
                [
                    "model_record_id",
                    "observation_date",
                    "flood_label",
                ]
            ],
            on="model_record_id",
            how="outer",
            suffixes=(
                "_label",
                "_source",
            ),
            indicator=True,
            validate="one_to_one",
        )
    )

    unmatched = (
        comparison[
            "_merge"
        ] != "both"
    )

    if unmatched.any():

        raise ValueError(
            f"{source_name} does not "
            "align one-to-one with labels. "
            f"Unmatched rows: "
            f"{int(unmatched.sum()):,}"
        )

    date_match = (
        comparison[
            "observation_date_label"
        ]
        == comparison[
            "observation_date_source"
        ]
    )

    if not date_match.all():

        raise ValueError(
            f"{source_name} contains "
            "observation-date mismatches."
        )

    label_match = (
        comparison[
            "flood_label_label"
        ].astype(int)
        == comparison[
            "flood_label_source"
        ].astype(int)
    )

    if not label_match.all():

        raise ValueError(
            f"{source_name} contains "
            "flood-label mismatches."
        )


# ============================================================
# Final dataset assembly
# ============================================================

def assemble_model_dataset(
    labels: pd.DataFrame,
    weather: pd.DataFrame,
    environmental: pd.DataFrame,
) -> pd.DataFrame:

    available_metadata = [
        column
        for column
        in METADATA_COLUMNS
        if column
        in labels.columns
    ]

    dataset = (
        labels[
            available_metadata
            + [
                "flood_label",
            ]
        ]
        .copy()
    )

    weather_columns = (
        [
            "model_record_id",
            "era5_grid_distance_km",
        ]
        + WEATHER_PREDICTORS
    )

    environmental_columns = (
        [
            "model_record_id",
            "static_reference_positive_id",
            "ghsl_epoch",
            "ghsl_built_surface_m2",
            "ghsl_sampling_method",
        ]
        + ENVIRONMENTAL_PREDICTORS
    )

    dataset = (
        dataset
        .merge(
            weather[
                weather_columns
            ],
            on="model_record_id",
            how="left",
            validate="one_to_one",
        )
        .merge(
            environmental[
                environmental_columns
            ],
            on="model_record_id",
            how="left",
            validate="one_to_one",
        )
    )

    # Put flood_label at the end.
    target = (
        dataset.pop(
            "flood_label"
        )
        .astype(int)
    )

    dataset[
        "flood_label"
    ] = target

    return dataset


# ============================================================
# Feature validation
# ============================================================

def validate_predictors(
    dataset: pd.DataFrame,
) -> dict:

    if dataset[
        "model_record_id"
    ].duplicated().any():

        raise ValueError(
            "Final modelling dataset "
            "contains duplicate model IDs."
        )

    if not set(
        dataset[
            "flood_label"
        ].unique()
    ).issubset(
        {0, 1}
    ):

        raise ValueError(
            "Final flood_label values "
            "must contain only 0 and 1."
        )

    class_counts = (
        dataset[
            "flood_label"
        ]
        .value_counts()
    )

    if not {
        0,
        1,
    }.issubset(
        set(
            class_counts.index
        )
    ):

        raise ValueError(
            "Both flood and presumed "
            "non-flood classes are required."
        )

    if (
        class_counts.get(
            0,
            0,
        )
        != class_counts.get(
            1,
            0,
        )
    ):

        raise ValueError(
            "Expected a balanced baseline "
            "modelling dataset."
        )

    for column in PREDICTOR_COLUMNS:

        dataset[
            column
        ] = pd.to_numeric(
            dataset[
                column
            ],
            errors="coerce",
        )

    missing_counts = (
        dataset[
            PREDICTOR_COLUMNS
        ]
        .isna()
        .sum()
    )

    missing_counts = (
        missing_counts[
            missing_counts > 0
        ]
    )

    if not missing_counts.empty:

        raise ValueError(
            "Model predictors contain "
            "missing values:\n"
            f"{missing_counts}"
        )

    predictor_array = (
        dataset[
            PREDICTOR_COLUMNS
        ]
        .to_numpy(
            dtype=float
        )
    )

    if not np.isfinite(
        predictor_array
    ).all():

        raise ValueError(
            "Model predictors contain "
            "infinite or non-finite values."
        )

    if not (
        (
            dataset[
                "rain_1d"
            ]
            <= dataset[
                "rain_3d"
            ]
        )
        & (
            dataset[
                "rain_3d"
            ]
            <= dataset[
                "rain_7d"
            ]
        )
    ).all():

        raise ValueError(
            "CHIRPS rainfall accumulation "
            "consistency check failed."
        )

    if not dataset[
        "relative_humidity_mean_pct"
    ].between(
        0,
        100,
    ).all():

        raise ValueError(
            "Relative humidity contains "
            "values outside 0-100%."
        )

    if (
        dataset[
            "era5_precip_1d_mm"
        ] < 0
    ).any():

        raise ValueError(
            "ERA5 precipitation contains "
            "negative values."
        )

    if (
        dataset[
            "soil_moisture_layer1_mean"
        ] < 0
    ).any():

        raise ValueError(
            "Soil moisture layer 1 "
            "contains negative values."
        )

    if (
        dataset[
            "soil_moisture_layer2_mean"
        ] < 0
    ).any():

        raise ValueError(
            "Soil moisture layer 2 "
            "contains negative values."
        )

    if (
        dataset[
            "slope_deg"
        ] < 0
    ).any():

        raise ValueError(
            "Slope contains negative values."
        )

    if not dataset[
        "flood_hazard_score"
    ].between(
        0,
        1,
    ).all():

        raise ValueError(
            "Flood hazard score contains "
            "values outside 0-1."
        )

    if not dataset[
        "ghsl_built_up_fraction"
    ].between(
        0,
        1.0001,
    ).all():

        raise ValueError(
            "GHSL built-up fraction "
            "contains values outside 0-1."
        )

    # Ensure we have not accidentally included
    # duplicate predictor columns.
    identical_pairs = []

    for index, first in enumerate(
        PREDICTOR_COLUMNS
    ):

        for second in (
            PREDICTOR_COLUMNS[
                index + 1:
            ]
        ):

            first_values = (
                dataset[
                    first
                ]
                .to_numpy(
                    dtype=float
                )
            )

            second_values = (
                dataset[
                    second
                ]
                .to_numpy(
                    dtype=float
                )
            )

            if np.array_equal(
                first_values,
                second_values,
            ):

                identical_pairs.append(
                    (
                        first,
                        second,
                    )
                )

    if identical_pairs:

        raise ValueError(
            "Exact duplicate model "
            "predictor columns detected: "
            f"{identical_pairs}"
        )

    return {
        "positive_rows":
            int(
                class_counts.get(
                    1,
                    0,
                )
            ),

        "negative_rows":
            int(
                class_counts.get(
                    0,
                    0,
                )
            ),

        "missing_predictor_values":
            int(
                dataset[
                    PREDICTOR_COLUMNS
                ]
                .isna()
                .sum()
                .sum()
            ),

        "non_finite_predictor_values":
            int(
                (
                    ~np.isfinite(
                        predictor_array
                    )
                ).sum()
            ),

        "duplicate_model_ids":
            int(
                dataset[
                    "model_record_id"
                ]
                .duplicated()
                .sum()
            ),

        "identical_predictor_pairs":
            len(
                identical_pairs
            ),
    }


# ============================================================
# Feature manifest
# ============================================================

def build_feature_manifest() -> pd.DataFrame:

    rows = [
        {
            "feature_name":
                "rain_1d",

            "feature_group":
                "weather",

            "source":
                "CHIRPS v3",

            "temporal_definition":
                "Rainfall on D-1",

            "model_predictor":
                True,

            "notes":
                "Event day D excluded",
        },
        {
            "feature_name":
                "rain_3d",

            "feature_group":
                "weather",

            "source":
                "CHIRPS v3",

            "temporal_definition":
                "Sum D-3 through D-1",

            "model_predictor":
                True,

            "notes":
                "Antecedent rainfall",
        },
        {
            "feature_name":
                "rain_7d",

            "feature_group":
                "weather",

            "source":
                "CHIRPS v3",

            "temporal_definition":
                "Sum D-7 through D-1",

            "model_predictor":
                True,

            "notes":
                "Antecedent rainfall",
        },
        {
            "feature_name":
                "temperature_mean_c",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Mean temperature on D-1",

            "model_predictor":
                True,

            "notes":
                "",
        },
        {
            "feature_name":
                "temperature_min_c",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Minimum temperature on D-1",

            "model_predictor":
                True,

            "notes":
                "",
        },
        {
            "feature_name":
                "temperature_max_c",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Maximum temperature on D-1",

            "model_predictor":
                True,

            "notes":
                "",
        },
        {
            "feature_name":
                "relative_humidity_mean_pct",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Mean relative humidity on D-1",

            "model_predictor":
                True,

            "notes":
                "",
        },
        {
            "feature_name":
                "pressure_mean_hpa",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Mean surface pressure on D-1",

            "model_predictor":
                True,

            "notes":
                "",
        },
        {
            "feature_name":
                "era5_precip_1d_mm",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Total ERA5-Land precipitation on D-1",

            "model_predictor":
                True,

            "notes":
                "Secondary rainfall predictor",
        },
        {
            "feature_name":
                "soil_moisture_layer1_mean",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Mean layer-1 soil moisture on D-1",

            "model_predictor":
                True,

            "notes":
                "",
        },
        {
            "feature_name":
                "soil_moisture_layer2_mean",

            "feature_group":
                "weather",

            "source":
                "ERA5-Land",

            "temporal_definition":
                "Mean layer-2 soil moisture on D-1",

            "model_predictor":
                True,

            "notes":
                "",
        },
        {
            "feature_name":
                "elevation_m",

            "feature_group":
                "environmental",

            "source":
                "NASADEM",

            "temporal_definition":
                "Static",

            "model_predictor":
                True,

            "notes":
                "Location-matched static feature",
        },
        {
            "feature_name":
                "slope_deg",

            "feature_group":
                "environmental",

            "source":
                "NASADEM-derived slope",

            "temporal_definition":
                "Static",

            "model_predictor":
                True,

            "notes":
                "Location-matched static feature",
        },
        {
            "feature_name":
                "flood_hazard_score",

            "feature_group":
                "environmental",

            "source":
                "ICPAC",

            "temporal_definition":
                "Static",

            "model_predictor":
                True,

            "notes":
                "Normalized flood hazard raster score",
        },
        {
            "feature_name":
                "ghsl_built_up_fraction",

            "feature_group":
                "environmental",

            "source":
                "GHSL",

            "temporal_definition":
                "Latest GHSL epoch <= observation year",

            "model_predictor":
                True,

            "notes":
                "Time-correct historical urbanization",
        },

        # Explicit exclusions.
        {
            "feature_name":
                "ghsl_built_surface_m2",

            "feature_group":
                "environmental",

            "source":
                "GHSL",

            "temporal_definition":
                "Latest GHSL epoch <= observation year",

            "model_predictor":
                False,

            "notes":
                (
                    "Excluded because it is an exact "
                    "scaled equivalent of "
                    "ghsl_built_up_fraction"
                ),
        },
        {
            "feature_name":
                "worldcover_2021_class",

            "feature_group":
                "environmental",

            "source":
                "ESA WorldCover 2021",

            "temporal_definition":
                "2021 only",

            "model_predictor":
                False,

            "notes":
                (
                    "Excluded from historical baseline "
                    "to avoid temporal leakage"
                ),
        },
        {
            "feature_name":
                "ghsl_epoch",

            "feature_group":
                "provenance",

            "source":
                "GHSL",

            "temporal_definition":
                "Epoch identifier",

            "model_predictor":
                False,

            "notes":
                "Retained as provenance only",
        },
        {
            "feature_name":
                "era5_grid_distance_km",

            "feature_group":
                "provenance",

            "source":
                "ERA5-Land mapping",

            "temporal_definition":
                "Static mapping quality",

            "model_predictor":
                False,

            "notes":
                "Retained as spatial quality metadata",
        },
        {
            "feature_name":
                "latitude",

            "feature_group":
                "metadata",

            "source":
                "Groundsource",

            "temporal_definition":
                "Observation location",

            "model_predictor":
                False,

            "notes":
                "Excluded to reduce location memorization",
        },
        {
            "feature_name":
                "longitude",

            "feature_group":
                "metadata",

            "source":
                "Groundsource",

            "temporal_definition":
                "Observation location",

            "model_predictor":
                False,

            "notes":
                "Excluded to reduce location memorization",
        },
    ]

    return pd.DataFrame(
        rows
    )


# ============================================================
# Validation report
# ============================================================

def build_validation_report(
    dataset: pd.DataFrame,
    validation_metrics: dict,
) -> pd.DataFrame:

    metrics = [
        (
            "model_rows",
            len(dataset),
        ),
        (
            "predictor_count",
            len(
                PREDICTOR_COLUMNS
            ),
        ),
        (
            "weather_predictor_count",
            len(
                WEATHER_PREDICTORS
            ),
        ),
        (
            "environmental_predictor_count",
            len(
                ENVIRONMENTAL_PREDICTORS
            ),
        ),
        (
            "positive_rows",
            validation_metrics[
                "positive_rows"
            ],
        ),
        (
            "negative_rows",
            validation_metrics[
                "negative_rows"
            ],
        ),
        (
            "unique_model_ids",
            dataset[
                "model_record_id"
            ].nunique(),
        ),
        (
            "unique_split_groups",
            dataset[
                "split_group_id"
            ].nunique(),
        ),
        (
            "missing_predictor_values",
            validation_metrics[
                "missing_predictor_values"
            ],
        ),
        (
            "non_finite_predictor_values",
            validation_metrics[
                "non_finite_predictor_values"
            ],
        ),
        (
            "duplicate_model_ids",
            validation_metrics[
                "duplicate_model_ids"
            ],
        ),
        (
            "identical_predictor_pairs",
            validation_metrics[
                "identical_predictor_pairs"
            ],
        ),
    ]

    return pd.DataFrame(
        metrics,
        columns=[
            "metric",
            "value",
        ],
    )


# ============================================================
# Main builder
def build_final_model_dataset():

    (
        labels,
        weather,
        environmental,
    ) = load_sources()

    validate_source_alignment(
        labels,
        weather,
        "Weather features",
    )

    validate_source_alignment(
        labels,
        environmental,
        "Environmental features",
    )

    dataset = (
        assemble_model_dataset(
            labels,
            weather,
            environmental,
        )
    )

    validation_metrics = (
        validate_predictors(
            dataset
        )
    )

    feature_matrix = (
        dataset[
            [
                "model_record_id",
                *PREDICTOR_COLUMNS,
                "flood_label",
            ]
        ]
        .copy()
    )

    expected_columns = [
        "model_record_id",
        *PREDICTOR_COLUMNS,
        "flood_label",
    ]

    if (
        feature_matrix.columns.tolist()
        != expected_columns
    ):

        raise ValueError(
            "Feature matrix schema "
            "does not match the approved "
            "predictor schema."
        )

    manifest = (
        build_feature_manifest()
    )

    selected_manifest_features = (
        manifest.loc[
            manifest[
                "model_predictor"
            ]
            == True,
            "feature_name",
        ]
        .tolist()
    )

    if (
        selected_manifest_features
        != PREDICTOR_COLUMNS
    ):

        raise ValueError(
            "Feature manifest and "
            "predictor schema disagree."
        )

    report = (
        build_validation_report(
            dataset,
            validation_metrics,
        )
    )

    MODEL_DATASET_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    MODEL_REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataset.to_csv(
        FULL_DATASET_FILE,
        index=False,
        date_format="%Y-%m-%d",
    )

    feature_matrix.to_csv(
        FEATURE_MATRIX_FILE,
        index=False,
    )

    manifest.to_csv(
        FEATURE_MANIFEST_FILE,
        index=False,
    )

    report.to_csv(
        VALIDATION_REPORT_FILE,
        index=False,
    )

    return (
        dataset,
        feature_matrix,
        manifest,
        report,
    )