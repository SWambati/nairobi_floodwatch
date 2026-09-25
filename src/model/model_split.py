from pathlib import Path
import numpy as np
import pandas as pd
from config.paths import PROCESSED_DIR
from src.model.model_dataset import (PREDICTOR_COLUMNS)


# Split configuration
TRAIN_RATIO = 0.70
VALIDATION_RATIO = 0.15
TEST_RATIO = 0.15
RANDOM_SEED = 42

# Search deterministic group permutations and retain the one closest to the requested 70/15/15 row distribution.
SPLIT_SEARCH_TRIALS = 10_000


# Path Configs
FEATURES_DIR = (PROCESSED_DIR.parent/ "features")
MODEL_DATASET_FILE = (FEATURES_DIR/ "modeling"/ "dataset"/ "historical_model_dataset.csv")
SPLIT_FEATURE_DIR = (FEATURES_DIR/ "modeling"/ "splits")
SPLIT_REPORT_DIR = (PROCESSED_DIR/ "modeling"/ "splits")
SPLIT_DATASET_FILE = (SPLIT_FEATURE_DIR/ "historical_model_dataset_with_splits.csv")
TRAIN_FILE = (SPLIT_FEATURE_DIR/ "train_model_features.csv")
VALIDATION_FILE = (SPLIT_FEATURE_DIR/ "validation_model_features.csv")
TEST_FILE = (SPLIT_FEATURE_DIR/ "test_model_features.csv")
ASSIGNMENT_FILE = (SPLIT_REPORT_DIR/ "model_split_assignments.csv")
SPLIT_REPORT_FILE = (SPLIT_REPORT_DIR/ "model_split_report.csv")


# Generic helpers
def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], dataset_name: str) -> None:
    missing = (required_columns - set(dataframe.columns))

    if missing:
        raise ValueError(f"{dataset_name} is missing required columns: {sorted(missing)}")


def _normalise_identifier(series: pd.Series) -> pd.Series:
    result = (series.astype("string").str.strip())
    result = result.replace(
        {
            "": pd.NA,
            "nan":pd.NA,
            "None":pd.NA,
            "<NA>":pd.NA,
        }
    )

    return result


# Dataset loading
def load_model_dataset() -> pd.DataFrame:

    dataset = pd.read_csv(MODEL_DATASET_FILE)

    required_columns = {
        "model_record_id",
        "flood_label",
        "event_group_id",
        "reference_positive_id",
        "reference_positive_event_group_id",
        "observation_date",
        *PREDICTOR_COLUMNS,
    }

    _require_columns(dataset, required_columns, "Historical model dataset",)

    dataset["observation_date"] = pd.to_datetime(dataset["observation_date"], errors="coerce",).dt.normalize()

    if dataset["observation_date"].isna().any():
        raise ValueError("Historical model dataset contains invalid observation dates.")

    if dataset["model_record_id"].duplicated().any():
        raise ValueError("Historical model dataset contains duplicate model IDs.")

    if not set(dataset["flood_label"].unique()).issubset({0, 1}):
        raise ValueError("flood_label must contain only 0 and 1.")

    dataset["event_group_id"] = _normalise_identifier(dataset["event_group_id"])
    dataset["reference_positive_id"] = _normalise_identifier(dataset["reference_positive_id"])
    dataset["reference_positive_event_group_id"] = _normalise_identifier(dataset["reference_positive_event_group_id"])

    return dataset


# Leakage-safe grouping
def build_final_split_groups(dataset: pd.DataFrame,) -> pd.DataFrame:

    result = dataset.copy()

    positive_mask = (result["flood_label"] == 1)
    negative_mask = (result["flood_label"] == 0)

    if result.loc[positive_mask, "event_group_id",].isna().any():
        raise ValueError("Positive observations contain missing event_group_id values.")

    if result.loc[negative_mask, "reference_positive_event_group_id",].isna().any():
        raise ValueError("Negative observations contain missing reference positive event-group IDs.")

    result["model_split_group_id"] = pd.NA
    result.loc[positive_mask, "model_split_group_id"] = ("EVENT_" + result.loc[positive_mask, "event_group_id",].astype(str))
    result.loc[negative_mask, "model_split_group_id"] = ("EVENT_" + result.loc[negative_mask, "reference_positive_event_group_id"].astype(str))

    if result["model_split_group_id"].isna().any():
        raise ValueError("Unable to construct model split groups for all rows.")

    # Every event group should contain the same number of positive and paired-negative rows.
    group_balance = (result.groupby("model_split_group_id")["flood_label"].agg(positive_rows=lambda values: int((values == 1).sum()), negative_rows=lambda values: int((values == 0).sum()),))
    unbalanced_groups = (group_balance[group_balance["positive_rows"]!= group_balance["negative_rows"]])

    if not unbalanced_groups.empty:
        raise ValueError(f"Some split groups do not contain matching positive and negative observation counts. Invalid groups: {len(unbalanced_groups):,}")

    return result


# Group summary
def build_group_summary(dataset: pd.DataFrame) -> pd.DataFrame:
    summary = (dataset.groupby("model_split_group_id", as_index=False).agg(row_count=("model_record_id", "size",), positive_rows=("flood_label", lambda values: int((values == 1).sum()),), negative_rows=("flood_label", lambda values: int((values == 0).sum()),), earliest_date=("observation_date", "min",), latest_date=("observation_date", "max",),))

    if not (summary["positive_rows"] == summary["negative_rows"]).all():
        raise ValueError("Split-group class balance validation failed.")

    return summary


# Split search
def _best_cutoff(row_counts: np.ndarray, target_rows: float) -> int:
    cumulative = np.cumsum(row_counts)
    differences = np.abs(cumulative - target_rows)
    cutoff_index = int(np.argmin(differences))

    # +1 because array index 0 means one group is included.
    return cutoff_index + 1


def search_group_split(group_summary: pd.DataFrame) -> dict[str, str]:
    total_rows = int(group_summary["row_count"].sum())
    train_target = (total_rows * TRAIN_RATIO)
    validation_target = (total_rows * VALIDATION_RATIO)
    test_target = (total_rows * TEST_RATIO)

    rng = np.random.default_rng(RANDOM_SEED)

    groups = (group_summary["model_split_group_id"].to_numpy())

    row_counts = (group_summary["row_count"].to_numpy(dtype=int))

    best_assignment = None
    best_score = None

    for _ in range(SPLIT_SEARCH_TRIALS):
        permutation = (rng.permutation(len(groups)))
        shuffled_groups = (groups[permutation])
        shuffled_counts = (row_counts[permutation])
        train_cutoff = (_best_cutoff(shuffled_counts, train_target))

        # leave at least two groups one for validation and one for test.
        train_cutoff = min(train_cutoff, len(shuffled_groups) - 2,)

        if train_cutoff <= 0:
            continue

        remaining_groups = (shuffled_groups[train_cutoff:])
        remaining_counts = (shuffled_counts[train_cutoff:])
        validation_cutoff = (_best_cutoff(remaining_counts, validation_target))
        validation_cutoff = min(validation_cutoff, len(remaining_groups) - 1)

        if validation_cutoff <= 0:
            continue

        train_groups = (shuffled_groups[:train_cutoff])
        validation_groups = (remaining_groups[:validation_cutoff])
        test_groups = (remaining_groups[validation_cutoff:])
        train_rows = int(shuffled_counts[:train_cutoff].sum())
        validation_rows = int(remaining_counts[:validation_cutoff].sum())
        test_rows = int(remaining_counts[validation_cutoff:].sum())

        if (len(train_groups) == 0 or len(validation_groups) == 0 or len(test_groups) == 0):
            continue

        score = (abs(train_rows - train_target) + abs(validation_rows - validation_target) + abs(test_rows - test_target))

        if (best_score is None or score < best_score):
            best_score = score
            best_assignment = {}

            for group_id in (train_groups):
                best_assignment[str(group_id)] = "train"

            for group_id in (validation_groups):
                best_assignment[str(group_id)] = "validation"

            for group_id in (test_groups):
                best_assignment[str(group_id)] = "test"

        # An exact row-count match cannot be improved upon.
        if score == 0:
            break

    if best_assignment is None:
        raise RuntimeError("Unable to construct a valid group-aware dataset split.")

    return best_assignment


# Apply assignments
def apply_split_assignments(dataset: pd.DataFrame, assignment: dict[str, str]) -> pd.DataFrame:
    result = dataset.copy()

    result["dataset_split"] = (result["model_split_group_id"].astype(str).map(assignment))

    if result["dataset_split"].isna().any():
        raise ValueError("Some modelling observations were not assigned to a split.")

    valid_splits = {
        "train",
        "validation",
        "test",
    }

    if set(result["dataset_split"].unique()) != valid_splits:
        raise ValueError("Final dataset must contain train, validation and test splits.")

    return result


# Leakage validation
def validate_group_isolation(dataset: pd.DataFrame) -> dict:
    split_names = [
        "train",
        "validation",
        "test",
    ]

    group_sets = {split: set(dataset.loc[dataset["dataset_split"] == split, "model_split_group_id",])for split in split_names}

    train_validation_overlap = (group_sets["train"] & group_sets["validation"])
    train_test_overlap = (group_sets["train"] & group_sets["test"])
    validation_test_overlap = (group_sets["validation"] & group_sets["test"])

    if (train_validation_overlap or train_test_overlap or validation_test_overlap):
        raise ValueError("Split-group leakage detected.")


    # Verify every negative remains with its paired positive.
    positive_split_map = (
        dataset.loc[dataset["flood_label"] == 1, ["model_record_id", "dataset_split",],].set_index("model_record_id")["dataset_split"].to_dict())

    negatives = (dataset[dataset["flood_label"] == 0].copy())

    negatives["reference_positive_split"] = (negatives["reference_positive_id"].map(positive_split_map))

    if negatives["reference_positive_split"].isna().any():
        raise ValueError("At least one negative references an unknown positive observation.")

    matched_pair_leaks = (negatives["dataset_split" ]!= negatives["reference_positive_split"])

    if matched_pair_leaks.any():
        raise ValueError("Paired positive-negative observations cross dataset splits.")


    # Verify each original positive flood event remain entirely inside one split.
    positive_events = (
        dataset[
            dataset[
                "flood_label"
            ] == 1
        ]
        .groupby(
            "event_group_id"
        )[
            "dataset_split"
        ]
        .nunique()
    )

    event_leaks = (positive_events[positive_events > 1])

    if not event_leaks.empty:
        raise ValueError("Positive event-group leakage detected across dataset splits.")

    return {
        "train_validation_group_overlap": len(train_validation_overlap),
        "train_test_group_overlap": len(train_test_overlap),
        "validation_test_group_overlap": len(validation_test_overlap),
        "matched_pair_leaks": int(matched_pair_leaks.sum()),
        "positive_event_group_leaks": len(event_leaks),
    }


# Split feature files
def build_feature_subset(dataset: pd.DataFrame, split_name: str) -> pd.DataFrame:
    subset = (
        dataset[
            dataset[
                "dataset_split"
            ] == split_name
        ][
            [
                "model_record_id",
                "model_split_group_id",
                *PREDICTOR_COLUMNS,
                "flood_label",
            ]
        ].copy().reset_index(drop=True))

    if subset.empty:
        raise ValueError(f"{split_name} split is empty.")

    return subset


# Split report
def build_split_report(dataset: pd.DataFrame, leakage_metrics: dict) -> pd.DataFrame:
    total_rows = len(dataset)

    rows = []

    for split_name in [
        "train",
        "validation",
        "test",
    ]:

        subset = (dataset[dataset["dataset_split"] == split_name])

        positive_rows = int((subset["flood_label"] == 1).sum())
        negative_rows = int((subset["flood_label"] == 0).sum())

        rows.extend(
            [
                (
                    f"{split_name}_rows",
                    len(
                        subset
                    ),
                ),
                (
                    f"{split_name}_ratio",
                    round(
                        len(
                            subset
                        )
                        / total_rows,
                        4,
                    ),
                ),
                (
                    f"{split_name}_positive_rows",
                    positive_rows,
                ),
                (
                    f"{split_name}_negative_rows",
                    negative_rows,
                ),
                (
                    f"{split_name}_groups",
                    subset[
                        "model_split_group_id"
                    ].nunique(),
                ),
            ]
        )

    rows.extend(
        [
            (
                "total_rows",
                total_rows,
            ),
            (
                "total_split_groups",
                dataset[
                    "model_split_group_id"
                ].nunique(),
            ),
            (
                "requested_train_ratio",
                TRAIN_RATIO,
            ),
            (
                "requested_validation_ratio",
                VALIDATION_RATIO,
            ),
            (
                "requested_test_ratio",
                TEST_RATIO,
            ),
            (
                "random_seed",
                RANDOM_SEED,
            ),
            (
                "split_search_trials",
                SPLIT_SEARCH_TRIALS,
            ),
        ]
    )

    for key, value in (leakage_metrics.items()):
        rows.append((key, value))

    return pd.DataFrame(rows, columns=["metric", "value",],)


# Assignment table
def build_assignment_table(dataset: pd.DataFrame,) -> pd.DataFrame:
    assignments = (
        dataset[
            [
                "model_record_id",
                "flood_label",
                "event_group_id",
                "reference_positive_id",
                "reference_positive_event_group_id",
                "model_split_group_id",
                "observation_date",
                "dataset_split",
            ]].copy())

    assignments = (
        assignments
        .sort_values(
            by=[
                "dataset_split",
                "model_split_group_id",
                "model_record_id",
            ]
        ).reset_index(drop=True))

    return assignments


# Main split function
def split_historical_model_dataset():
    dataset = (load_model_dataset())
    dataset = (build_final_split_groups(dataset))
    group_summary = (build_group_summary(dataset))
    assignment = (search_group_split(group_summary))
    split_dataset = (apply_split_assignments(dataset, assignment,))
    leakage_metrics = (validate_group_isolation(split_dataset))
    train = (build_feature_subset(split_dataset, "train",))
    validation = (build_feature_subset(split_dataset, "validation",))
    test = (build_feature_subset(split_dataset, "test",))
    report = (build_split_report(split_dataset, leakage_metrics,))
    assignments = (build_assignment_table(split_dataset))

    SPLIT_FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    SPLIT_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    split_dataset.to_csv(SPLIT_DATASET_FILE, index=False, date_format="%Y-%m-%d",)
    train.to_csv(TRAIN_FILE, index=False)
    validation.to_csv(VALIDATION_FILE, index=False)
    test.to_csv(TEST_FILE, index=False)
    assignments.to_csv(ASSIGNMENT_FILE, index=False, date_format="%Y-%m-%d",)
    report.to_csv(SPLIT_REPORT_FILE, index=False)

    return (
        split_dataset,
        train,
        validation,
        test,
        group_summary,
        report,
    )