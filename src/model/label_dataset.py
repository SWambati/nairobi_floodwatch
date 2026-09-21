from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
from config.paths import PROCESSED_DIR


# Paths
FLOOD_LABEL_DIR = (PROCESSED_DIR/ "flood_labels")
MODELING_LABEL_DIR = (PROCESSED_DIR/ "modeling"/ "labels")
POSITIVE_SOURCE_FILE = (FLOOD_LABEL_DIR/ "groundsource_positive_candidates.csv")
MASTER_ANCHOR_FILE = (FLOOD_LABEL_DIR/ "master_event_anchors.csv")

POSITIVE_OUTPUT_FILE = (MODELING_LABEL_DIR/ "positive_model_observations.csv")
NEGATIVE_OUTPUT_FILE = (MODELING_LABEL_DIR/ "negative_model_observations.csv")
COMBINED_OUTPUT_FILE = (MODELING_LABEL_DIR/ "historical_model_labels.csv")
REPORT_OUTPUT_FILE = (MODELING_LABEL_DIR/ "label_construction_report.csv")


# Historical modelling window

# ERA5-Land begins on 2004-01-01.
# D-7 to D-1 predictors require seven previous complete days.
HISTORICAL_START = pd.Timestamp("2004-01-08")

# ERA5-Land historical archive currently ends here.
HISTORICAL_END = pd.Timestamp("2025-12-31")

EVENT_BUFFER_DAYS = 7

# Antecedent rainfall window used by the model.
ANTECEDENT_DAYS = 7

RANDOM_SEED = 42

COORDINATE_DECIMALS = 5


POSITIVE_REQUIRED_COLUMNS = {
    "label_record_id",
    "event_group_id",
    "start_date",
    "end_date",
    "latitude",
    "longitude",
    "flood_label",
    "verification_status",
}


MASTER_REQUIRED_COLUMNS = {
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


VERIFICATION_PRIORITY = {
    "candidate": 1,
    "near_match": 2,
    "corroborated": 3,
}



# Validation helpers

def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], dataset_name: str) -> None:
    missing_columns = (required_columns - set(dataframe.columns))

    if missing_columns:
        raise ValueError(f"{dataset_name} is missing required columns: {sorted(missing_columns)}")

def _normalise_date_column(dataframe: pd.DataFrame, column_name: str) -> None:
    dataframe[column_name] = (pd.to_datetime(dataframe[column_name], errors="coerce").dt.normalize())


# Input loading
def load_label_sources() -> tuple[pd.DataFrame, pd.DataFrame]:

    positives = pd.read_csv(POSITIVE_SOURCE_FILE)
    master = pd.read_csv(MASTER_ANCHOR_FILE)

    _require_columns(positives, POSITIVE_REQUIRED_COLUMNS, "Groundsource positive candidates")
    _require_columns(master, MASTER_REQUIRED_COLUMNS, "Master event anchors")

    for column in ["start_date", "end_date"]:
        _normalise_date_column(positives, column)

    for column in ["start_date", "end_date", "anchor_date"]:
        _normalise_date_column(master, column)

    if positives["start_date"].isna().any():
        raise ValueError("Positive flood observations contain missing start dates.")

    positives["end_date"] = positives["end_date"].fillna(positives["start_date"])
    master["start_date"] = master["start_date"].fillna(master["anchor_date"])
    master["end_date"] = master["end_date"].fillna(master["start_date"])

    if not (positives["flood_label"] == 1).all():
        raise ValueError("Groundsource positive source contains records where flood_label != 1.")

    if not (master["flood_label"] == 1).all():
        raise ValueError("Master event catalogue contains records where flood_label != 1.")

    return positives, master


# Positive observation construction
def consolidate_positive_observations(positives: pd.DataFrame) -> pd.DataFrame:
    working = positives.copy()
    working["_latitude_key"] = working["latitude"].round(COORDINATE_DECIMALS)
    working["_longitude_key"] = working["longitude"].round(COORDINATE_DECIMALS)

    observation_key = ["_latitude_key", "_longitude_key", "start_date"]

    # A true duplicate modelling observation should not unexpectedly belong to multiple event groups.
    event_group_counts = (working.groupby(observation_key, dropna=False)["event_group_id"].nunique(dropna=True))
    conflicting_duplicates = (event_group_counts[event_group_counts > 1])

    if not conflicting_duplicates.empty:
        raise ValueError("Duplicate location-date flood observations belong to multiple event groups. Review them before automatic consolidation.")

    report_counts = (working.groupby(observation_key, dropna=False).size().rename("source_report_count").reset_index())
    source_ids = (working.groupby(observation_key, dropna=False)["label_record_id"].agg(lambda values: "|".join(sorted(values.astype(str).tolist()))).rename("source_label_record_ids").reset_index())
    working["_verification_rank"] = (working["verification_status"].map(VERIFICATION_PRIORITY).fillna(0))

    working = working.sort_values(by=["_verification_rank", "label_record_id",], ascending=[False,True,],)

    consolidated = (working.drop_duplicates(subset=observation_key, keep="first",).copy())

    consolidated = (
        consolidated
        .merge(
            report_counts,
            on=observation_key,
            how="left",
            validate="one_to_one",
        )
        .merge(
            source_ids,
            on=observation_key,
            how="left",
            validate="one_to_one",
        )
    )

    consolidated = (
        consolidated
        .sort_values(
            by=[
                "start_date",
                "label_record_id",
            ]
        )
        .reset_index(
            drop=True
        )
    )

    consolidated[
        "model_record_id"
    ] = [
        f"POS_{index:06d}"
        for index in range(
            1,
            len(consolidated) + 1,
        )
    ]

    consolidated[
        "source_label_record_id"
    ] = consolidated[
        "label_record_id"
    ].astype(str)

    consolidated[
        "observation_date"
    ] = consolidated[
        "start_date"
    ]

    consolidated[
        "split_group_id"
    ] = (
        "POS_"
        + consolidated[
            "event_group_id"
        ].astype(str)
    )

    consolidated[
        "label_source"
    ] = (
        "groundsource_positive"
    )

    consolidated[
        "record_role"
    ] = (
        "model_positive"
    )

    consolidated[
        "negative_sampling_strategy"
    ] = pd.NA

    consolidated[
        "reference_positive_id"
    ] = consolidated[
        "model_record_id"
    ]

    consolidated[
        "reference_positive_event_group_id"
    ] = consolidated[
        "event_group_id"
    ]

    consolidated[
        "year_distance_from_reference"
    ] = 0

    consolidated[
        "weather_join_ready"
    ] = True

    consolidated = consolidated.drop(
        columns=[
            "_latitude_key",
            "_longitude_key",
            "_verification_rank",
        ],
        errors="ignore",
    )

    return consolidated


# Flood exclusion calendar


def _collect_event_intervals(
    positives: pd.DataFrame,
    master: pd.DataFrame,
) -> list[
    tuple[pd.Timestamp, pd.Timestamp]
]:

    intervals = []

    for row in positives.itertuples(
        index=False
    ):

        start = row.start_date
        end = row.end_date

        if pd.isna(end):
            end = start

        intervals.append(
            (
                pd.Timestamp(start),
                pd.Timestamp(end),
            )
        )

    for row in master.itertuples(
        index=False
    ):

        dates = [
            value
            for value in [
                row.start_date,
                row.end_date,
                row.anchor_date,
            ]
            if pd.notna(value)
        ]

        if not dates:
            continue

        intervals.append(
            (
                min(dates),
                max(dates),
            )
        )

    return intervals


def build_blocked_target_dates(
    positives: pd.DataFrame,
    master: pd.DataFrame,
) -> pd.DatetimeIndex:

    intervals = _collect_event_intervals(
        positives,
        master,
    )

    blocked_dates = set()

    for start, end in intervals:

        # Seven days before the event are protected.
        blocked_start = (
            start
            - pd.Timedelta(
                days=EVENT_BUFFER_DAYS
            )
        )

        # Seven days after the event are protected, plus another seven days because D-7..D-1 must also avoid the protected flood period.
        blocked_end = (
            end
            + pd.Timedelta(
                days=(
                    EVENT_BUFFER_DAYS
                    + ANTECEDENT_DAYS
                )
            )
        )

        blocked_start = max(
            blocked_start,
            HISTORICAL_START,
        )

        blocked_end = min(
            blocked_end,
            HISTORICAL_END,
        )

        if blocked_start > blocked_end:
            continue

        for date in pd.date_range(
            blocked_start,
            blocked_end,
            freq="D",
        ):
            blocked_dates.add(
                date.normalize()
            )

    return pd.DatetimeIndex(
        sorted(blocked_dates)
    )



# Conservative negative sampling
def build_negative_observations(positives: pd.DataFrame, blocked_dates: pd.DatetimeIndex) -> pd.DataFrame:

    all_dates = pd.date_range(
        HISTORICAL_START,
        HISTORICAL_END,
        freq="D",
    )

    safe_dates = all_dates[
        ~all_dates.isin(
            blocked_dates
        )
    ]

    if safe_dates.empty:

        raise ValueError(
            "No safe historical dates "
            "remain after flood exclusion."
        )

    rng = np.random.default_rng(
        RANDOM_SEED
    )

    used_location_dates = set()

    # Helps prevent every location from being assigned the same small number of dates.
    date_usage = Counter()

    negative_rows = []

    positives = (
        positives
        .sort_values(
            by=[
                "observation_date",
                "model_record_id",
            ]
        )
        .reset_index(
            drop=True
        )
    )

    for index, row in positives.iterrows():

        positive_date = pd.Timestamp(
            row[
                "observation_date"
            ]
        )

        positive_month = (
            positive_date.month
        )

        positive_year = (
            positive_date.year
        )

        latitude_key = round(
            float(row["latitude"]),
            COORDINATE_DECIMALS,
        )

        longitude_key = round(
            float(row["longitude"]),
            COORDINATE_DECIMALS,
        )

        month_candidates = (
            safe_dates[
                safe_dates.month
                == positive_month
            ]
        )

        available_candidates = []

        for candidate in month_candidates:

            pair_key = (
                latitude_key,
                longitude_key,
                candidate.date(),
            )

            if (
                pair_key
                not in used_location_dates
            ):
                available_candidates.append(
                    candidate
                )

        if not available_candidates:

            raise ValueError(
                "Unable to find a safe "
                "season-matched negative date "
                f"for {row['model_record_id']}."
            )

        candidate_index = (
            pd.DatetimeIndex(
                available_candidates
            )
        )

        year_distances = np.abs(
            candidate_index.year
            - positive_year
        )

        minimum_year_distance = int(
            year_distances.min()
        )

        nearest_candidates = (
            candidate_index[
                year_distances
                == minimum_year_distance
            ]
        )

        minimum_usage = min(
            date_usage[
                candidate
            ]
            for candidate
            in nearest_candidates
        )

        least_used_candidates = [
            candidate
            for candidate
            in nearest_candidates
            if date_usage[candidate]
            == minimum_usage
        ]

        selected_position = int(
            rng.integers(
                0,
                len(
                    least_used_candidates
                ),
            )
        )

        selected_date = (
            least_used_candidates[
                selected_position
            ]
        )

        used_location_dates.add(
            (
                latitude_key,
                longitude_key,
                selected_date.date(),
            )
        )

        date_usage[
            selected_date
        ] += 1

        negative_id = (
            f"NEG_{index + 1:06d}"
        )

        split_group_id = (
            "NEG_"
            + selected_date.strftime(
                "%Y%m%d"
            )
        )

        negative_rows.append(
            {
                "model_record_id":
                    negative_id,

                "source_label_record_id":
                    pd.NA,

                "source_label_record_ids":
                    pd.NA,

                "source_report_count":
                    0,

                "event_group_id":
                    pd.NA,

                "split_group_id":
                    split_group_id,

                "observation_date":
                    selected_date,

                "start_date":
                    selected_date,

                "end_date":
                    selected_date,

                "anchor_date":
                    pd.NaT,

                "latitude":
                    row["latitude"],

                "longitude":
                    row["longitude"],

                "location_text":
                    row.get(
                        "location_text",
                        pd.NA,
                    ),

                "flood_label":
                    0,

                "verification_status":
                    "presumed_non_flood",

                "label_source":
                    (
                        "derived_conservative_negative"
                    ),

                "source_name":
                    (
                        "derived_negative_sampling"
                    ),

                "source_event_id":
                    pd.NA,

                "confidence":
                    pd.NA,

                "primary_source":
                    pd.NA,

                "supporting_source":
                    pd.NA,

                "evidence_class":
                    pd.NA,

                "record_role":
                    "model_negative",

                "weather_join_ready":
                    True,

                "negative_sampling_strategy":
                    (
                        "same_location_same_month_"
                        "nearest_year_citywide_"
                        "event_exclusion"
                    ),

                "reference_positive_id":
                    row[
                        "model_record_id"
                    ],

                "reference_positive_event_group_id":
                    row[
                        "event_group_id"
                    ],

                "year_distance_from_reference":
                    minimum_year_distance,
            }
        )

    return pd.DataFrame(
        negative_rows
    )


# Final validation
def validate_model_labels(
    positives: pd.DataFrame,
    negatives: pd.DataFrame,
    combined: pd.DataFrame,
    blocked_dates: pd.DatetimeIndex,
) -> None:

    if positives[
        "model_record_id"
    ].duplicated().any():

        raise ValueError(
            "Positive model IDs "
            "are not unique."
        )

    if negatives[
        "model_record_id"
    ].duplicated().any():

        raise ValueError(
            "Negative model IDs "
            "are not unique."
        )

    if combined[
        "model_record_id"
    ].duplicated().any():

        raise ValueError(
            "Combined model IDs "
            "are not unique."
        )

    if not (
        positives["flood_label"]
        == 1
    ).all():

        raise ValueError(
            "Positive model observations "
            "contain non-positive labels."
        )

    if not (
        negatives["flood_label"]
        == 0
    ).all():

        raise ValueError(
            "Negative model observations "
            "contain non-zero labels."
        )

    if len(positives) != len(negatives):

        raise ValueError(
            "Baseline modelling dataset "
            "must use a 1:1 positive-to-negative "
            "sampling ratio."
        )

    if negatives[
        "observation_date"
    ].isin(
        blocked_dates
    ).any():

        raise ValueError(
            "At least one negative sample "
            "falls inside a protected "
            "flood-event target window."
        )

    if (
        negatives["observation_date"]
        < HISTORICAL_START
    ).any():

        raise ValueError(
            "Negative sample predates "
            "historical modelling coverage."
        )

    if (
        negatives["observation_date"]
        > HISTORICAL_END
    ).any():

        raise ValueError(
            "Negative sample exceeds "
            "historical modelling coverage."
        )

    reference = positives[
        [
            "model_record_id",
            "observation_date",
            "latitude",
            "longitude",
        ]
    ].copy()

    reference = reference.rename(
        columns={
            "model_record_id":
                "reference_positive_id",

            "observation_date":
                "reference_observation_date",

            "latitude":
                "reference_latitude",

            "longitude":
                "reference_longitude",
        }
    )

    comparison = negatives.merge(
        reference,
        on="reference_positive_id",
        how="left",
        validate="many_to_one",
    )

    if comparison[
        "reference_observation_date"
    ].isna().any():

        raise ValueError(
            "Negative sample references "
            "an unknown positive observation."
        )

    same_month = (
        comparison[
            "observation_date"
        ].dt.month
        == comparison[
            "reference_observation_date"
        ].dt.month
    )

    if not same_month.all():

        raise ValueError(
            "Negative samples are not "
            "calendar-month matched."
        )

    same_latitude = np.isclose(
        comparison["latitude"],
        comparison[
            "reference_latitude"
        ],
    )

    same_longitude = np.isclose(
        comparison["longitude"],
        comparison[
            "reference_longitude"
        ],
    )

    if not (
        same_latitude
        & same_longitude
    ).all():

        raise ValueError(
            "Negative samples are not "
            "location matched to their "
            "reference positives."
        )

    positive_keys = set(
        zip(
            positives[
                "latitude"
            ].round(
                COORDINATE_DECIMALS
            ),
            positives[
                "longitude"
            ].round(
                COORDINATE_DECIMALS
            ),
            positives[
                "observation_date"
            ],
        )
    )

    negative_keys = set(
        zip(
            negatives[
                "latitude"
            ].round(
                COORDINATE_DECIMALS
            ),
            negatives[
                "longitude"
            ].round(
                COORDINATE_DECIMALS
            ),
            negatives[
                "observation_date"
            ],
        )
    )

    overlap = (
        positive_keys
        & negative_keys
    )

    if overlap:

        raise ValueError(
            "Positive and negative "
            "location-date observations overlap."
        )



# Reporting
def build_construction_report(
    raw_positive_count: int,
    positives: pd.DataFrame,
    negatives: pd.DataFrame,
    combined: pd.DataFrame,
    blocked_dates: pd.DatetimeIndex,
) -> pd.DataFrame:

    all_dates = pd.date_range(
        HISTORICAL_START,
        HISTORICAL_END,
        freq="D",
    )

    safe_date_count = int(
        (
            ~all_dates.isin(
                blocked_dates
            )
        ).sum()
    )

    duplicate_rows_removed = (
        raw_positive_count
        - len(positives)
    )

    metrics = [
        (
            "raw_positive_rows",
            raw_positive_count,
        ),
        (
            "final_positive_rows",
            len(positives),
        ),
        (
            "duplicate_positive_rows_removed",
            duplicate_rows_removed,
        ),
        (
            "negative_rows",
            len(negatives),
        ),
        (
            "combined_model_rows",
            len(combined),
        ),
        (
            "positive_class_rows",
            int(
                (
                    combined[
                        "flood_label"
                    ]
                    == 1
                ).sum()
            ),
        ),
        (
            "negative_class_rows",
            int(
                (
                    combined[
                        "flood_label"
                    ]
                    == 0
                ).sum()
            ),
        ),
        (
            "unique_positive_event_groups",
            positives[
                "event_group_id"
            ].nunique(),
        ),
        (
            "unique_negative_dates",
            negatives[
                "observation_date"
            ].nunique(),
        ),
        (
            "blocked_target_dates",
            len(blocked_dates),
        ),
        (
            "safe_target_dates",
            safe_date_count,
        ),
        (
            "event_buffer_days",
            EVENT_BUFFER_DAYS,
        ),
        (
            "antecedent_window_days",
            ANTECEDENT_DAYS,
        ),
        (
            "negative_year_distance_mean",
            round(
                negatives[
                    "year_distance_from_reference"
                ].mean(),
                2,
            ),
        ),
        (
            "negative_year_distance_max",
            int(
                negatives[
                    "year_distance_from_reference"
                ].max()
            ),
        ),
        (
            "random_seed",
            RANDOM_SEED,
        ),
    ]

    return pd.DataFrame(
        metrics,
        columns=[
            "metric",
            "value",
        ],
    )



# Main construction function
def build_historical_model_labels():

    raw_positives, master = (
        load_label_sources()
    )

    raw_positive_count = len(
        raw_positives
    )

    positives = (
        consolidate_positive_observations(
            raw_positives
        )
    )

    blocked_dates = (
        build_blocked_target_dates(
            raw_positives,
            master,
        )
    )

    negatives = (
        build_negative_observations(
            positives,
            blocked_dates,
        )
    )

    core_columns = [
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
        "flood_label",
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

    for column in core_columns:

        if column not in positives.columns:
            positives[column] = pd.NA

        if column not in negatives.columns:
            negatives[column] = pd.NA

    positives = positives[
        core_columns
    ].copy()

    negatives = negatives[
        core_columns
    ].copy()

    combined = pd.concat(
        [
            positives,
            negatives,
        ],
        ignore_index=True,
    )

    combined = combined.sort_values(
        by=[
            "flood_label",
            "observation_date",
            "model_record_id",
        ],
        ascending=[
            False,
            True,
            True,
        ],
    ).reset_index(
        drop=True
    )

    validate_model_labels(
        positives,
        negatives,
        combined,
        blocked_dates,
    )

    report = (
        build_construction_report(
            raw_positive_count,
            positives,
            negatives,
            combined,
            blocked_dates,
        )
    )

    MODELING_LABEL_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    positives.to_csv(
        POSITIVE_OUTPUT_FILE,
        index=False,
        date_format="%Y-%m-%d",
    )

    negatives.to_csv(
        NEGATIVE_OUTPUT_FILE,
        index=False,
        date_format="%Y-%m-%d",
    )

    combined.to_csv(
        COMBINED_OUTPUT_FILE,
        index=False,
        date_format="%Y-%m-%d",
    )

    report.to_csv(
        REPORT_OUTPUT_FILE,
        index=False,
    )

    return (
        positives,
        negatives,
        combined,
        report,
    )