from pathlib import Path
from typing import Iterable

import pandas as pd

from config.paths import (
    GROUNDSOURCE_DIR,
    MASTER_CATALOGUE_DIR,
    PROCESSED_DIR,
)


# ============================================================
# SOURCE FILES
# ============================================================

GROUNDSOURCE_FILE = (
    GROUNDSOURCE_DIR
    / "groundsource_nairobi_localized_exact_boundary.csv"
)

MASTER_CATALOGUE_FILE = (
    MASTER_CATALOGUE_DIR
    / "nairobi_flood_master_2004_2025.csv"
)


# ============================================================
# OUTPUT LOCATION
# ============================================================

FLOOD_LABEL_OUTPUT_DIR = (
    PROCESSED_DIR
    / "flood_labels"
)


# ============================================================
# EXPECTED SOURCE COLUMNS
# ============================================================

GROUNDSOURCE_REQUIRED_COLUMNS = {
    "uuid",
    "start_date",
    "end_date",
    "centroid_longitude",
    "centroid_latitude",
    "spatial_class",
    "spatial_confidence",
    "confidence_tier",
    "stage2_match_status",
    "stage2_match_ids",
    "temporal_leakage_group",
    "flood_label",
    "model_use",
}


MASTER_REQUIRED_COLUMNS = {
    "Master_Event_ID",
    "Evidence_Start",
    "Evidence_End",
    "Anchor_Date",
    "Locations",
    "Primary_Source",
    "Supporting_Source",
    "Confidence",
    "Evidence_Class",
    "Flood_Label",
    "Train_Test_Group",
    "Weather_Join_Ready",
    "Notes",
    "Reference_URL",
}


# ============================================================
# VALIDATION HELPERS
# ============================================================

def validate_required_columns(
    dataframe: pd.DataFrame,
    required_columns: Iterable[str],
    dataset_name: str,
) -> None:
    """
    Ensure that a source dataset contains every column
    required by the flood-label ETL module.
    """

    required = set(required_columns)
    actual = set(dataframe.columns)

    missing = required - actual

    if missing:
        raise ValueError(
            f"{dataset_name} is missing required columns: "
            f"{sorted(missing)}"
        )


def validate_date_order(
    dataframe: pd.DataFrame,
    start_column: str,
    end_column: str,
    dataset_name: str,
) -> None:
    """
    Ensure that no event ends before it starts.
    """

    invalid = dataframe[
        dataframe[end_column].notna()
        & (
            dataframe[end_column]
            < dataframe[start_column]
        )
    ]

    if not invalid.empty:
        raise ValueError(
            f"{dataset_name} contains "
            f"{len(invalid)} records where "
            "end date occurs before start date."
        )


def validate_flood_labels(
    dataframe: pd.DataFrame,
    label_column: str,
    dataset_name: str,
) -> None:
    """
    These acquisition files should contain positive
    flood observations only.
    """

    invalid_labels = set(
        dataframe[label_column]
        .dropna()
        .unique()
    ) - {1}

    if invalid_labels:
        raise ValueError(
            f"{dataset_name} contains unexpected "
            f"flood labels: {sorted(invalid_labels)}"
        )


def validate_coordinates(
    dataframe: pd.DataFrame,
) -> None:
    """
    Validate Groundsource centroid coordinates.
    Exact Nairobi boundary verification was already
    performed during acquisition.
    """

    missing_coordinates = dataframe[
        dataframe["centroid_latitude"].isna()
        | dataframe["centroid_longitude"].isna()
    ]

    if not missing_coordinates.empty:
        raise ValueError(
            "Groundsource contains records with "
            "missing centroid coordinates."
        )

    invalid_latitude = ~dataframe[
        "centroid_latitude"
    ].between(-90, 90)

    invalid_longitude = ~dataframe[
        "centroid_longitude"
    ].between(-180, 180)

    if (
        invalid_latitude.any()
        or invalid_longitude.any()
    ):
        raise ValueError(
            "Groundsource contains invalid coordinates."
        )


# ============================================================
# LOADERS
# ============================================================

def load_groundsource(
    file_path: Path = GROUNDSOURCE_FILE,
) -> pd.DataFrame:
    """
    Load the finalized Nairobi Groundsource subset.
    """

    if not file_path.exists():
        raise FileNotFoundError(
            f"Groundsource file not found: {file_path}"
        )

    dataframe = pd.read_csv(file_path)

    validate_required_columns(
        dataframe,
        GROUNDSOURCE_REQUIRED_COLUMNS,
        "Groundsource",
    )

    return dataframe


def load_master_catalogue(
    file_path: Path = MASTER_CATALOGUE_FILE,
) -> pd.DataFrame:
    """
    Load the finalized Nairobi cross-source
    master flood-event catalogue.
    """

    if not file_path.exists():
        raise FileNotFoundError(
            f"Master catalogue not found: {file_path}"
        )

    dataframe = pd.read_csv(file_path)

    validate_required_columns(
        dataframe,
        MASTER_REQUIRED_COLUMNS,
        "Master catalogue",
    )

    return dataframe


# ============================================================
# GROUNDSOURCE PREPROCESSING
# ============================================================

def preprocess_groundsource(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert finalized Groundsource observations into
    the project's canonical flood-label representation.

    This function intentionally does NOT:
      - create negative samples
      - deduplicate storm events
      - join weather data
      - use geometry area as a model predictor
    """

    dataframe = dataframe.copy()

    # --------------------------------------------------------
    # Basic integrity
    # --------------------------------------------------------

    if dataframe["uuid"].duplicated().any():
        raise ValueError(
            "Groundsource contains duplicate UUID values."
        )

    # --------------------------------------------------------
    # Dates
    # --------------------------------------------------------

    dataframe["start_date"] = pd.to_datetime(
        dataframe["start_date"],
        errors="raise",
    ).dt.date

    dataframe["end_date"] = pd.to_datetime(
        dataframe["end_date"],
        errors="raise",
    ).dt.date

    validate_date_order(
        dataframe,
        "start_date",
        "end_date",
        "Groundsource",
    )

    validate_flood_labels(
        dataframe,
        "flood_label",
        "Groundsource",
    )

    validate_coordinates(dataframe)

    # --------------------------------------------------------
    # Internal identifiers
    # --------------------------------------------------------

    dataframe["label_record_id"] = (
        "GS-" + dataframe["uuid"].astype(str)
    )

    dataframe["source_name"] = "Groundsource"

    dataframe["source_event_id"] = (
        dataframe["uuid"].astype(str)
    )

    # temporal_leakage_group already represents
    # observations that must remain together during
    # train/test splitting.
    dataframe["event_group_id"] = (
        dataframe["temporal_leakage_group"]
    )

    # --------------------------------------------------------
    # Verification status
    # --------------------------------------------------------

    verification_map = {
    "MASTER_INTERVAL_MATCH": "corroborated",
    "MASTER_WITHIN_1_DAY": "corroborated",
    "MASTER_WITHIN_3_DAYS": "near_match",
    "NO_STAGE2_MATCH": "candidate",
    }
    dataframe["verification_status"] = (
    dataframe["stage2_match_status"]
    .map(verification_map)
    )
    if dataframe["verification_status"].isna().any():
        unknown_statuses = (
        dataframe.loc[
            dataframe["verification_status"].isna(),
            "stage2_match_status",
        ]
        .dropna()
        .unique()
        .tolist()
    )

    raise ValueError(
        "Groundsource contains unexpected "
        f"Stage-2 match statuses: {unknown_statuses}"
    )

    # Every retained Groundsource record has coordinates,
    # making it suitable for later weather/spatial joins.
    dataframe["weather_join_ready"] = True

    dataframe["record_role"] = (
        "localized_positive_candidate"
    )

    # --------------------------------------------------------
    # Canonical output
    # --------------------------------------------------------

    output_columns = [
        "label_record_id",
        "source_name",
        "source_event_id",
        "event_group_id",
        "start_date",
        "end_date",
        "centroid_latitude",
        "centroid_longitude",
        "flood_label",
        "verification_status",
        "spatial_confidence",
        "confidence_tier",
        "stage2_match_status",
        "stage2_match_ids",
        "weather_join_ready",
        "model_use",
        "record_role",
    ]

    result = dataframe[
        output_columns
    ].rename(
        columns={
            "centroid_latitude": "latitude",
            "centroid_longitude": "longitude",
        }
    )

    return result.reset_index(drop=True)


# ============================================================
# MASTER CATALOGUE PREPROCESSING
# ============================================================

def preprocess_master_catalogue(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert the curated cross-source master catalogue
    into a consistent event-anchor representation.

    These records are corroboration anchors and must
    NOT automatically be counted as additional
    independent positive observations.
    """

    dataframe = dataframe.copy()

    # --------------------------------------------------------
    # Unique event identifiers
    # --------------------------------------------------------

    if dataframe[
        "Master_Event_ID"
    ].duplicated().any():
        raise ValueError(
            "Master catalogue contains duplicate "
            "Master_Event_ID values."
        )

    # --------------------------------------------------------
    # Dates
    # --------------------------------------------------------

    for column in [
        "Evidence_Start",
        "Evidence_End",
        "Anchor_Date",
    ]:
        dataframe[column] = pd.to_datetime(
            dataframe[column],
            errors="raise",
        ).dt.date

    validate_date_order(
        dataframe,
        "Evidence_Start",
        "Evidence_End",
        "Master catalogue",
    )

    validate_flood_labels(
        dataframe,
        "Flood_Label",
        "Master catalogue",
    )

    # --------------------------------------------------------
    # Internal representation
    # --------------------------------------------------------

    dataframe["label_record_id"] = (
        "MASTER-"
        + dataframe["Master_Event_ID"].astype(str)
    )

    dataframe["source_name"] = (
        "Nairobi Flood Master Catalogue"
    )

    dataframe["source_event_id"] = (
        dataframe["Master_Event_ID"]
    )

    dataframe["event_group_id"] = (
        dataframe["Train_Test_Group"]
    )

    dataframe["verification_status"] = (
        "verified_anchor"
    )

    dataframe["weather_join_ready"] = (
        dataframe["Weather_Join_Ready"]
        .astype(str)
        .str.upper()
        .str.startswith("YES")
    )

    dataframe["record_role"] = (
        "cross_source_event_anchor"
    )

    # --------------------------------------------------------
    # Canonical output
    # --------------------------------------------------------

    result = pd.DataFrame(
        {
            "label_record_id":
                dataframe["label_record_id"],

            "source_name":
                dataframe["source_name"],

            "source_event_id":
                dataframe["source_event_id"],

            "event_group_id":
                dataframe["event_group_id"],

            "start_date":
                dataframe["Evidence_Start"],

            "end_date":
                dataframe["Evidence_End"],

            "anchor_date":
                dataframe["Anchor_Date"],

            "location_text":
                dataframe["Locations"],

            "flood_label":
                dataframe["Flood_Label"],

            "verification_status":
                dataframe["verification_status"],

            "confidence":
                dataframe["Confidence"],

            "primary_source":
                dataframe["Primary_Source"],

            "supporting_source":
                dataframe["Supporting_Source"],

            "evidence_class":
                dataframe["Evidence_Class"],

            "weather_join_ready":
                dataframe["weather_join_ready"],

            "notes":
                dataframe["Notes"],

            "reference_url":
                dataframe["Reference_URL"],

            "record_role":
                dataframe["record_role"],
        }
    )

    return result.reset_index(drop=True)


# ============================================================
# PIPELINE ENTRY POINT
# ============================================================

def process_flood_labels() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    """
    Run the flood-label ingestion/preprocessing stage.

    Returns:
        groundsource_labels
        master_event_anchors
    """

    print(
        "Loading finalized flood-label datasets..."
    )

    groundsource_raw = load_groundsource()
    master_raw = load_master_catalogue()

    print(
        f"Groundsource raw records: "
        f"{len(groundsource_raw):,}"
    )

    print(
        f"Master catalogue records: "
        f"{len(master_raw):,}"
    )

    groundsource_processed = (
        preprocess_groundsource(
            groundsource_raw
        )
    )

    master_processed = (
        preprocess_master_catalogue(
            master_raw
        )
    )

    return (
        groundsource_processed,
        master_processed,
    )

# OUTPUT WRITER
def save_flood_label_outputs(
    groundsource_labels: pd.DataFrame,
    master_event_anchors: pd.DataFrame,
) -> None:
    """
    Persist ETL-ready flood-label outputs.
    """

    FLOOD_LABEL_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    groundsource_output = (
        FLOOD_LABEL_OUTPUT_DIR
        / "groundsource_positive_candidates.csv"
    )

    master_output = (
        FLOOD_LABEL_OUTPUT_DIR
        / "master_event_anchors.csv"
    )

    groundsource_labels.to_csv(
        groundsource_output,
        index=False,
    )

    master_event_anchors.to_csv(
        master_output,
        index=False,
    )

    print(
        f"Saved Groundsource labels: "
        f"{groundsource_output}"
    )

    print(
        f"Saved master event anchors: "
        f"{master_output}"
    )