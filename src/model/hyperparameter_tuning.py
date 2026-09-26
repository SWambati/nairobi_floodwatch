import hashlib
import json
import time
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    auc,
    average_precision_score,
    confusion_matrix,
    f1_score,
    make_scorer,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import (RandomizedSearchCV, StratifiedGroupKFold)
from xgboost import XGBClassifier
from config.paths import PROCESSED_DIR
from src.model.model_dataset import (PREDICTOR_COLUMNS)


# Configuration
RANDOM_SEED = 42
CV_FOLDS = 5
RF_SEARCH_ITERATIONS = 40
XGB_SEARCH_ITERATIONS = 40
DECISION_THRESHOLD = 0.50


# Hyperparameter search spaces
RF_PARAMETER_DISTRIBUTIONS = {
    "n_estimators": [
        200,
        300,
        500,
        700,
    ],

    "max_depth": [
        None,
        5,
        8,
        12,
        16,
        20,
    ],

    "min_samples_split": [
        2,
        4,
        6,
        10,
        15,
    ],

    "min_samples_leaf": [
        1,
        2,
        3,
        4,
        6,
        8,
    ],

    "max_features": [
        "sqrt",
        "log2",
        0.5,
        0.75,
    ],

    "bootstrap": [
        True,
    ],
}


XGB_PARAMETER_DISTRIBUTIONS = {
    "n_estimators": [
        100,
        200,
        300,
        500,
        700,
    ],

    "learning_rate": [
        0.01,
        0.03,
        0.05,
        0.10,
        0.20,
    ],

    "max_depth": [
        2,
        3,
        4,
        5,
        6,
    ],

    "min_child_weight": [
        1,
        2,
        4,
        6,
        8,
    ],

    "gamma": [
        0.0,
        0.1,
        0.25,
        0.5,
        1.0,
    ],

    "subsample": [
        0.60,
        0.75,
        0.90,
        1.0,
    ],

    "colsample_bytree": [
        0.60,
        0.75,
        0.90,
        1.0,
    ],

    "reg_alpha": [
        0.0,
        0.01,
        0.1,
        0.5,
        1.0,
    ],

    "reg_lambda": [
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
    ],
}



# Paths
PROJECT_OUTPUT_DIR = (
    PROCESSED_DIR.parent
)

FEATURES_DIR = (
    PROJECT_OUTPUT_DIR
    / "features"
)

SPLIT_DIR = (
    FEATURES_DIR
    / "modeling"
    / "splits"
)

TRAIN_FILE = (
    SPLIT_DIR
    / "train_model_features.csv"
)

VALIDATION_FILE = (
    SPLIT_DIR
    / "validation_model_features.csv"
)


# Tuned model artifacts
RF_MODEL_DIR = (
    PROJECT_OUTPUT_DIR
    / "models"
    / "random_forest"
    / "tuned"
)

XGB_MODEL_DIR = (
    PROJECT_OUTPUT_DIR
    / "models"
    / "xgboost"
    / "tuned"
)

RF_MODEL_FILE = (
    RF_MODEL_DIR
    / "random_forest_tuned.joblib"
)

XGB_MODEL_FILE = (
    XGB_MODEL_DIR
    / "xgboost_tuned.json"
)

RF_METADATA_FILE = (
    RF_MODEL_DIR
    / "random_forest_tuned_metadata.json"
)

XGB_METADATA_FILE = (
    XGB_MODEL_DIR
    / "xgboost_tuned_metadata.json"
)


# Tuning reports
TUNING_REPORT_DIR = (
    PROCESSED_DIR
    / "modeling"
    / "tuning"
)

RF_SEARCH_RESULTS_FILE = (
    TUNING_REPORT_DIR
    / "random_forest_search_results.csv"
)

XGB_SEARCH_RESULTS_FILE = (
    TUNING_REPORT_DIR
    / "xgboost_search_results.csv"
)

CV_FOLD_REPORT_FILE = (
    TUNING_REPORT_DIR
    / "group_cv_fold_report.csv"
)

TUNED_METRICS_FILE = (
    TUNING_REPORT_DIR
    / "tuned_model_metrics.csv"
)

TUNED_COMPARISON_FILE = (
    TUNING_REPORT_DIR
    / "tuned_validation_comparison.csv"
)

BEST_PARAMS_FILE = (
    TUNING_REPORT_DIR
    / "best_hyperparameters.json"
)

RF_CONFUSION_MATRIX_FILE = (
    TUNING_REPORT_DIR
    / "random_forest_tuned_validation_confusion_matrix.csv"
)

XGB_CONFUSION_MATRIX_FILE = (
    TUNING_REPORT_DIR
    / "xgboost_tuned_validation_confusion_matrix.csv"
)

RF_FEATURE_IMPORTANCE_FILE = (
    TUNING_REPORT_DIR
    / "random_forest_tuned_feature_importance.csv"
)

XGB_FEATURE_IMPORTANCE_FILE = (
    TUNING_REPORT_DIR
    / "xgboost_tuned_feature_importance.csv"
)

RF_VALIDATION_PREDICTIONS_FILE = (
    TUNING_REPORT_DIR
    / "random_forest_tuned_validation_predictions.csv"
)

XGB_VALIDATION_PREDICTIONS_FILE = (
    TUNING_REPORT_DIR
    / "xgboost_tuned_validation_predictions.csv"
)


# Existing baseline metrics
RF_BASELINE_METRICS_FILE = (
    PROCESSED_DIR
    / "modeling"
    / "random_forest"
    / "baseline"
    / "random_forest_baseline_metrics.csv"
)

XGB_BASELINE_METRICS_FILE = (
    PROCESSED_DIR
    / "modeling"
    / "xgboost"
    / "baseline"
    / "xgboost_baseline_metrics.csv"
)


# Scoring
SCORING = {
    "accuracy":
        "accuracy",

    "precision":
        make_scorer(
            precision_score,
            zero_division=0,
        ),

    "recall":
        make_scorer(
            recall_score,
            zero_division=0,
        ),

    "f1":
        make_scorer(
            f1_score,
            zero_division=0,
        ),

    "roc_auc":
        "roc_auc",

    "average_precision":
        "average_precision",
}


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


def _json_safe(
    value,
):

    if isinstance(
        value,
        dict,
    ):

        return {
            str(key):
                _json_safe(
                    item
                )
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):

        return [
            _json_safe(
                item
            )
            for item in value
        ]

    if isinstance(
        value,
        np.integer,
    ):

        return int(
            value
        )

    if isinstance(
        value,
        np.floating,
    ):

        return float(
            value
        )

    if isinstance(
        value,
        np.bool_,
    ):

        return bool(
            value
        )

    return value


def calculate_file_sha256(
    file_path: Path,
) -> str:

    sha256 = hashlib.sha256()

    with file_path.open(
        "rb"
    ) as file:

        for block in iter(
            lambda:
            file.read(
                1024 * 1024
            ),
            b"",
        ):

            sha256.update(
                block
            )

    return sha256.hexdigest()


# Load dataset
def load_model_splits() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:

    train = pd.read_csv(
        TRAIN_FILE
    )

    validation = pd.read_csv(
        VALIDATION_FILE
    )

    required_columns = {
        "model_record_id",
        "model_split_group_id",
        "flood_label",
        *PREDICTOR_COLUMNS,
    }

    _require_columns(
        train,
        required_columns,
        "Training dataset",
    )

    _require_columns(
        validation,
        required_columns,
        "Validation dataset",
    )

    for dataset_name, dataframe in [
        (
            "Training dataset",
            train,
        ),
        (
            "Validation dataset",
            validation,
        ),
    ]:

        if dataframe[
            "model_record_id"
        ].duplicated().any():

            raise ValueError(
                f"{dataset_name} contains "
                "duplicate model IDs."
            )

        classes = set(
            dataframe[
                "flood_label"
            ].unique()
        )

        if classes != {
            0,
            1,
        }:

            raise ValueError(
                f"{dataset_name} must "
                "contain both binary classes."
            )

        for column in (
            PREDICTOR_COLUMNS
        ):

            dataframe[
                column
            ] = pd.to_numeric(
                dataframe[
                    column
                ],
                errors="coerce",
            )

        if dataframe[
            PREDICTOR_COLUMNS
        ].isna().any().any():

            raise ValueError(
                f"{dataset_name} contains "
                "missing predictor values."
            )

        values = (
            dataframe[
                PREDICTOR_COLUMNS
            ]
            .to_numpy(
                dtype=float
            )
        )

        if not np.isfinite(
            values
        ).all():

            raise ValueError(
                f"{dataset_name} contains "
                "non-finite predictor values."
            )

    train_groups = set(
        train[
            "model_split_group_id"
        ]
    )

    validation_groups = set(
        validation[
            "model_split_group_id"
        ]
    )

    overlap = (
        train_groups
        & validation_groups
    )

    if overlap:

        raise ValueError(
            "Training and validation "
            "group leakage detected."
        )

    return (
        train,
        validation,
    )


def extract_xy_groups(
    dataframe: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.Series,
    pd.Series,
]:

    x = dataframe[
        PREDICTOR_COLUMNS
    ].copy()

    y = dataframe[
        "flood_label"
    ].astype(
        int
    )

    groups = dataframe[
        "model_split_group_id"
    ].astype(
        str
    )

    return (
        x,
        y,
        groups,
    )



# Group-aware validation
def build_cv_strategy():

    return StratifiedGroupKFold(
        n_splits=CV_FOLDS,
        shuffle=True,
        random_state=RANDOM_SEED,
    )


def validate_cv_folds(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    groups: pd.Series,
) -> pd.DataFrame:

    cv = build_cv_strategy()

    rows = []

    for fold_number, (
        training_index,
        validation_index,
    ) in enumerate(
        cv.split(
            x_train,
            y_train,
            groups,
        ),
        start=1,
    ):

        fold_train_y = (
            y_train.iloc[
                training_index
            ]
        )

        fold_validation_y = (
            y_train.iloc[
                validation_index
            ]
        )

        fold_train_groups = set(
            groups.iloc[
                training_index
            ]
        )

        fold_validation_groups = set(
            groups.iloc[
                validation_index
            ]
        )

        overlap = (
            fold_train_groups
            & fold_validation_groups
        )

        if overlap:

            raise ValueError(
                f"Group leakage detected "
                f"inside CV fold "
                f"{fold_number}."
            )

        if fold_train_y.nunique() != 2:

            raise ValueError(
                f"CV fold {fold_number} "
                "training partition does "
                "not contain both classes."
            )

        if (
            fold_validation_y.nunique()
            != 2
        ):

            raise ValueError(
                f"CV fold {fold_number} "
                "validation partition does "
                "not contain both classes."
            )

        rows.append(
            {
                "fold":
                    fold_number,

                "train_rows":
                    len(
                        training_index
                    ),

                "validation_rows":
                    len(
                        validation_index
                    ),

                "train_groups":
                    len(
                        fold_train_groups
                    ),

                "validation_groups":
                    len(
                        fold_validation_groups
                    ),

                "train_flood":
                    int(
                        (
                            fold_train_y
                            == 1
                        ).sum()
                    ),

                "train_non_flood":
                    int(
                        (
                            fold_train_y
                            == 0
                        ).sum()
                    ),

                "validation_flood":
                    int(
                        (
                            fold_validation_y
                            == 1
                        ).sum()
                    ),

                "validation_non_flood":
                    int(
                        (
                            fold_validation_y
                            == 0
                        ).sum()
                    ),

                "group_overlap":
                    len(
                        overlap
                    ),
            }
        )

    return pd.DataFrame(
        rows
    )


# Search definitions
def build_random_forest_search():

    estimator = (
        RandomForestClassifier(
            random_state=(RANDOM_SEED),
            class_weight=None,
            n_jobs=1,
        )
    )

    return RandomizedSearchCV(
        estimator=estimator,

        param_distributions=(
            RF_PARAMETER_DISTRIBUTIONS
        ),

        n_iter=(
            RF_SEARCH_ITERATIONS
        ),

        scoring=SCORING,

        refit="f1",

        cv=build_cv_strategy(),

        random_state=(
            RANDOM_SEED
        ),

        n_jobs=-1,

        verbose=1,

        return_train_score=True,

        error_score="raise",
    )


def build_xgboost_search():

    estimator = (
        XGBClassifier(
            objective=(
                "binary:logistic"
            ),

            eval_metric=(
                "logloss"
            ),

            tree_method="hist",

            scale_pos_weight=1.0,

            random_state=(
                RANDOM_SEED
            ),

            # Avoid nested parallelism.
            n_jobs=1,

            verbosity=0,
        )
    )

    return RandomizedSearchCV(
        estimator=estimator,

        param_distributions=(
            XGB_PARAMETER_DISTRIBUTIONS
        ),

        n_iter=(
            XGB_SEARCH_ITERATIONS
        ),

        scoring=SCORING,

        refit="f1",

        cv=build_cv_strategy(),

        random_state=(
            RANDOM_SEED
        ),

        n_jobs=-1,

        verbose=1,

        return_train_score=True,

        error_score="raise",
    )

# Model Evaluation
def calculate_metrics(
    model_name: str,
    split_name: str,
    y_true: pd.Series,
    probabilities: np.ndarray,
) -> tuple[
    dict,
    np.ndarray,
]:

    predictions = (
        probabilities
        >= DECISION_THRESHOLD
    ).astype(
        int
    )

    matrix = confusion_matrix(
        y_true,
        predictions,
        labels=[
            0,
            1,
        ],
    )

    tn, fp, fn, tp = (
        matrix.ravel()
    )

    (
        precision_curve,
        recall_curve,
        _,
    ) = precision_recall_curve(
        y_true,
        probabilities,
    )

    metrics = {
        "model":
            model_name,

        "split":
            split_name,

        "threshold":
            DECISION_THRESHOLD,

        "rows":
            len(
                y_true
            ),

        "accuracy":
            accuracy_score(
                y_true,
                predictions,
            ),

        "precision":
            precision_score(
                y_true,
                predictions,
                zero_division=0,
            ),

        "recall":
            recall_score(
                y_true,
                predictions,
                zero_division=0,
            ),

        "f1":
            f1_score(
                y_true,
                predictions,
                zero_division=0,
            ),

        "roc_auc":
            roc_auc_score(
                y_true,
                probabilities,
            ),

        "pr_auc":
            auc(
                recall_curve,
                precision_curve,
            ),

        "average_precision":
            average_precision_score(
                y_true,
                probabilities,
            ),

        "true_negative":
            int(
                tn
            ),

        "false_positive":
            int(
                fp
            ),

        "false_negative":
            int(
                fn
            ),

        "true_positive":
            int(
                tp
            ),
    }

    return (
        metrics,
        predictions,
    )


def evaluate_estimator(
    model_name: str,
    estimator,
    train: pd.DataFrame,
    validation: pd.DataFrame,
) -> dict:

    x_train = train[
        PREDICTOR_COLUMNS
    ]

    y_train = train[
        "flood_label"
    ].astype(
        int
    )

    x_validation = validation[
        PREDICTOR_COLUMNS
    ]

    y_validation = validation[
        "flood_label"
    ].astype(
        int
    )

    train_probabilities = (
        estimator.predict_proba(
            x_train
        )[:, 1]
    )

    (
        train_metrics,
        train_predictions,
    ) = calculate_metrics(
        model_name,
        "train",
        y_train,
        train_probabilities,
    )

    inference_start = (
        time.perf_counter()
    )

    validation_probabilities = (
        estimator.predict_proba(
            x_validation
        )[:, 1]
    )

    inference_seconds = (
        time.perf_counter()
        - inference_start
    )

    (
        validation_metrics,
        validation_predictions,
    ) = calculate_metrics(
        model_name,
        "validation",
        y_validation,
        validation_probabilities,
    )

    inference_ms_per_row = (
        inference_seconds
        / len(
            validation
        )
        * 1000
    )

    predictions = (
        validation[
            [
                "model_record_id",
                "model_split_group_id",
                "flood_label",
            ]
        ]
        .copy()
    )

    predictions[
        "flood_probability"
    ] = (
        validation_probabilities
    )

    predictions[
        "predicted_label"
    ] = (
        validation_predictions
    )

    predictions[
        "correct_prediction"
    ] = (
        predictions[
            "flood_label"
        ]
        == predictions[
            "predicted_label"
        ]
    )

    matrix = confusion_matrix(
        y_validation,
        validation_predictions,
        labels=[
            0,
            1,
        ],
    )

    confusion = pd.DataFrame(
        matrix,
        index=[
            "actual_non_flood",
            "actual_flood",
        ],
        columns=[
            "predicted_non_flood",
            "predicted_flood",
        ],
    )

    return {
        "train_metrics":
            train_metrics,

        "validation_metrics":
            validation_metrics,

        "predictions":
            predictions,

        "confusion_matrix":
            confusion,

        "validation_inference_seconds":
            inference_seconds,

        "validation_inference_ms_per_row":
            inference_ms_per_row,
    }


# Feature importance
def build_rf_feature_importance(
    model: RandomForestClassifier,
) -> pd.DataFrame:

    importance = pd.DataFrame(
        {
            "feature_name":
                PREDICTOR_COLUMNS,

            "importance":
                model.feature_importances_,
        }
    )

    importance = (
        importance
        .sort_values(
            by="importance",
            ascending=False,
        )
        .reset_index(
            drop=True
        )
    )

    importance[
        "rank"
    ] = np.arange(
        1,
        len(
            importance
        ) + 1,
    )

    return importance[
        [
            "rank",
            "feature_name",
            "importance",
        ]
    ]


def build_xgb_feature_importance(
    model: XGBClassifier,
) -> pd.DataFrame:

    booster = (
        model.get_booster()
    )

    gains = (
        booster.get_score(
            importance_type="gain"
        )
    )

    rows = []

    for feature_name in (
        PREDICTOR_COLUMNS
    ):

        rows.append(
            {
                "feature_name":
                    feature_name,

                "gain":
                    float(
                        gains.get(
                            feature_name,
                            0.0,
                        )
                    ),
            }
        )

    result = pd.DataFrame(
        rows
    )

    total_gain = (
        result[
            "gain"
        ].sum()
    )

    if total_gain > 0:

        result[
            "importance"
        ] = (
            result[
                "gain"
            ]
            / total_gain
        )

    else:

        result[
            "importance"
        ] = 0.0

    result = (
        result
        .sort_values(
            by="importance",
            ascending=False,
        )
        .reset_index(
            drop=True
        )
    )

    result[
        "rank"
    ] = np.arange(
        1,
        len(
            result
        ) + 1,
    )

    return result[
        [
            "rank",
            "feature_name",
            "gain",
            "importance",
        ]
    ]


# Cross validation search result helpers
def prepare_search_results(
    search,
) -> pd.DataFrame:

    results = pd.DataFrame(
        search.cv_results_
    )

    important_columns = [
        column
        for column
        in results.columns
        if (
            column.startswith(
                "param_"
            )
            or column.startswith(
                "mean_train_"
            )
            or column.startswith(
                "std_train_"
            )
            or column.startswith(
                "mean_test_"
            )
            or column.startswith(
                "std_test_"
            )
            or column.startswith(
                "rank_test_"
            )
        )
    ]

    results = results[
        important_columns
    ].copy()

    if (
        "rank_test_f1"
        in results.columns
    ):

        results = (
            results
            .sort_values(
                by="rank_test_f1"
            )
            .reset_index(
                drop=True
            )
        )

    return results


def best_cv_summary(
    search,
) -> dict:

    index = int(
        search.best_index_
    )

    results = (
        search.cv_results_
    )

    return {
        "best_cv_f1":
            float(
                results[
                    "mean_test_f1"
                ][index]
            ),

        "best_cv_f1_std":
            float(
                results[
                    "std_test_f1"
                ][index]
            ),

        "best_cv_train_f1":
            float(
                results[
                    "mean_train_f1"
                ][index]
            ),

        "best_cv_accuracy":
            float(
                results[
                    "mean_test_accuracy"
                ][index]
            ),

        "best_cv_precision":
            float(
                results[
                    "mean_test_precision"
                ][index]
            ),

        "best_cv_recall":
            float(
                results[
                    "mean_test_recall"
                ][index]
            ),

        "best_cv_roc_auc":
            float(
                results[
                    "mean_test_roc_auc"
                ][index]
            ),

        "best_cv_average_precision":
            float(
                results[
                    "mean_test_average_precision"
                ][index]
            ),
    }



# Baseline comparison
def load_baseline_validation_metrics(
    model_name: str,
) -> dict | None:

    if model_name == (
        "random_forest"
    ):

        path = (
            RF_BASELINE_METRICS_FILE
        )

    elif model_name == (
        "xgboost"
    ):

        path = (
            XGB_BASELINE_METRICS_FILE
        )

    else:

        raise ValueError(
            f"Unknown model: "
            f"{model_name}"
        )

    if not path.exists():

        return None

    metrics = pd.read_csv(
        path
    )

    validation = (
        metrics[
            metrics[
                "split"
            ] == "validation"
        ]
    )

    if validation.empty:

        return None

    row = (
        validation.iloc[0]
    )

    return {
        "accuracy":
            float(
                row[
                    "accuracy"
                ]
            ),

        "precision":
            float(
                row[
                    "precision"
                ]
            ),

        "recall":
            float(
                row[
                    "recall"
                ]
            ),

        "f1":
            float(
                row[
                    "f1"
                ]
            ),

        "roc_auc":
            float(
                row[
                    "roc_auc"
                ]
            ),

        "pr_auc":
            float(
                row[
                    "pr_auc"
                ]
            ),
    }



# Main tuning routine
def tune_models():

    (
        train,
        validation,
    ) = load_model_splits()

    (
        x_train,
        y_train,
        groups,
    ) = extract_xy_groups(
        train
    )

    cv_fold_report = (
        validate_cv_folds(
            x_train,
            y_train,
            groups,
        )
    )

    
    # Random Forest search
    rf_search = (
        build_random_forest_search()
    )

    rf_search_start = (
        time.perf_counter()
    )

    rf_search.fit(
        x_train,
        y_train,
        groups=groups,
    )

    rf_search_seconds = (
        time.perf_counter()
        - rf_search_start
    )

    best_rf = (
        rf_search.best_estimator_
    )

    
    # XGBoost search
    xgb_search = (
        build_xgboost_search()
    )

    xgb_search_start = (
        time.perf_counter()
    )

    xgb_search.fit(
        x_train,
        y_train,
        groups=groups,
    )

    xgb_search_seconds = (
        time.perf_counter()
        - xgb_search_start
    )

    best_xgb = (
        xgb_search.best_estimator_
    )

    
    # Validation evaluation
    rf_evaluation = (
        evaluate_estimator(
            "random_forest",
            best_rf,
            train,
            validation,
        )
    )

    xgb_evaluation = (
        evaluate_estimator(
            "xgboost",
            best_xgb,
            train,
            validation,
        )
    )

    rf_cv_summary = (
        best_cv_summary(
            rf_search
        )
    )

    xgb_cv_summary = (
        best_cv_summary(
            xgb_search
        )
    )


    # Search results
    rf_search_results = (
        prepare_search_results(
            rf_search
        )
    )

    xgb_search_results = (
        prepare_search_results(
            xgb_search
        )
    )

   
    # Combined metrics
    metrics = pd.DataFrame(
        [
            rf_evaluation[
                "train_metrics"
            ],

            rf_evaluation[
                "validation_metrics"
            ],

            xgb_evaluation[
                "train_metrics"
            ],

            xgb_evaluation[
                "validation_metrics"
            ],
        ]
    )

    
    # Validation comparison
    comparison_rows = []

    for (
        model_name,
        evaluation,
        cv_summary,
        search_seconds,
    ) in [
        (
            "random_forest",
            rf_evaluation,
            rf_cv_summary,
            rf_search_seconds,
        ),
        (
            "xgboost",
            xgb_evaluation,
            xgb_cv_summary,
            xgb_search_seconds,
        ),
    ]:

        validation_metrics = (
            evaluation[
                "validation_metrics"
            ]
        )

        baseline = (
            load_baseline_validation_metrics(
                model_name
            )
        )

        row = {
            "model":
                model_name,

            "cv_f1":
                cv_summary[
                    "best_cv_f1"
                ],

            "cv_f1_std":
                cv_summary[
                    "best_cv_f1_std"
                ],

            "cv_train_f1":
                cv_summary[
                    "best_cv_train_f1"
                ],

            "validation_accuracy":
                validation_metrics[
                    "accuracy"
                ],

            "validation_precision":
                validation_metrics[
                    "precision"
                ],

            "validation_recall":
                validation_metrics[
                    "recall"
                ],

            "validation_f1":
                validation_metrics[
                    "f1"
                ],

            "validation_roc_auc":
                validation_metrics[
                    "roc_auc"
                ],

            "validation_pr_auc":
                validation_metrics[
                    "pr_auc"
                ],

            "search_seconds":
                search_seconds,

            "validation_inference_ms_per_row":
                evaluation[
                    "validation_inference_ms_per_row"
                ],
        }

        if baseline is not None:

            row[
                "baseline_validation_f1"
            ] = baseline[
                "f1"
            ]

            row[
                "f1_change_from_baseline"
            ] = (
                validation_metrics[
                    "f1"
                ]
                - baseline[
                    "f1"
                ]
            )

            row[
                "baseline_validation_roc_auc"
            ] = baseline[
                "roc_auc"
            ]

            row[
                "roc_auc_change_from_baseline"
            ] = (
                validation_metrics[
                    "roc_auc"
                ]
                - baseline[
                    "roc_auc"
                ]
            )

            row[
                "baseline_validation_pr_auc"
            ] = baseline[
                "pr_auc"
            ]

            row[
                "pr_auc_change_from_baseline"
            ] = (
                validation_metrics[
                    "pr_auc"
                ]
                - baseline[
                    "pr_auc"
                ]
            )

        comparison_rows.append(
            row
        )

    comparison = pd.DataFrame(
        comparison_rows
    )

    # --------------------------------------------------------
    # Feature importance
    # --------------------------------------------------------

    rf_feature_importance = (
        build_rf_feature_importance(
            best_rf
        )
    )

    xgb_feature_importance = (
        build_xgb_feature_importance(
            best_xgb
        )
    )

   
    # Output directories
    RF_MODEL_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    XGB_MODEL_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    TUNING_REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    
    # Save models
    joblib.dump(
        best_rf,
        RF_MODEL_FILE,
    )

    best_xgb.save_model(
        str(
            XGB_MODEL_FILE
        )
    )

    rf_sha256 = (
        calculate_file_sha256(
            RF_MODEL_FILE
        )
    )

    xgb_sha256 = (
        calculate_file_sha256(
            XGB_MODEL_FILE
        )
    )

    # Save tuning reports
    rf_search_results.to_csv(
        RF_SEARCH_RESULTS_FILE,
        index=False,
    )

    xgb_search_results.to_csv(
        XGB_SEARCH_RESULTS_FILE,
        index=False,
    )

    cv_fold_report.to_csv(
        CV_FOLD_REPORT_FILE,
        index=False,
    )

    metrics.to_csv(
        TUNED_METRICS_FILE,
        index=False,
    )

    comparison.to_csv(
        TUNED_COMPARISON_FILE,
        index=False,
    )

    rf_evaluation[
        "confusion_matrix"
    ].to_csv(
        RF_CONFUSION_MATRIX_FILE
    )

    xgb_evaluation[
        "confusion_matrix"
    ].to_csv(
        XGB_CONFUSION_MATRIX_FILE
    )

    rf_feature_importance.to_csv(
        RF_FEATURE_IMPORTANCE_FILE,
        index=False,
    )

    xgb_feature_importance.to_csv(
        XGB_FEATURE_IMPORTANCE_FILE,
        index=False,
    )

    rf_evaluation[
        "predictions"
    ].to_csv(
        RF_VALIDATION_PREDICTIONS_FILE,
        index=False,
    )

    xgb_evaluation[
        "predictions"
    ].to_csv(
        XGB_VALIDATION_PREDICTIONS_FILE,
        index=False,
    )


    # Save best parameter file
    best_parameters = {
        "random_forest":
            _json_safe(
                rf_search.best_params_
            ),

        "xgboost":
            _json_safe(
                xgb_search.best_params_
            ),
    }

    with BEST_PARAMS_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            best_parameters,
            file,
            indent=2,
        )

    
    # Metadata
    rf_metadata = {
        "model_name":
            "random_forest_tuned",

        "predictor_count":
            len(
                PREDICTOR_COLUMNS
            ),

        "predictor_columns":
            PREDICTOR_COLUMNS,

        "training_rows":
            len(
                train
            ),

        "validation_rows":
            len(
                validation
            ),

        "test_rows_used":
            0,

        "cv_folds":
            CV_FOLDS,

        "cv_strategy":
            "StratifiedGroupKFold",

        "optimization_metric":
            "f1",

        "search_method":
            "RandomizedSearchCV",

        "search_iterations":
            RF_SEARCH_ITERATIONS,

        "best_parameters":
            _json_safe(
                rf_search.best_params_
            ),

        "best_cv_results":
            _json_safe(
                rf_cv_summary
            ),

        "decision_threshold":
            DECISION_THRESHOLD,

        "class_weight":
            None,

        "resampling":
            None,

        "search_seconds":
            rf_search_seconds,

        "random_seed":
            RANDOM_SEED,

        "sklearn_version":
            sklearn.__version__,

        "model_sha256":
            rf_sha256,
    }

    xgb_metadata = {
        "model_name":
            "xgboost_tuned",

        "predictor_count":
            len(
                PREDICTOR_COLUMNS
            ),

        "predictor_columns":
            PREDICTOR_COLUMNS,

        "training_rows":
            len(
                train
            ),

        "validation_rows":
            len(
                validation
            ),

        "test_rows_used":
            0,

        "cv_folds":
            CV_FOLDS,

        "cv_strategy":
            "StratifiedGroupKFold",

        "optimization_metric":
            "f1",

        "search_method":
            "RandomizedSearchCV",

        "search_iterations":
            XGB_SEARCH_ITERATIONS,

        "best_parameters":
            _json_safe(
                xgb_search.best_params_
            ),

        "best_cv_results":
            _json_safe(
                xgb_cv_summary
            ),

        "decision_threshold":
            DECISION_THRESHOLD,

        "scale_pos_weight":
            1.0,

        "resampling":
            None,

        "early_stopping":
            False,

        "search_seconds":
            xgb_search_seconds,

        "random_seed":
            RANDOM_SEED,

        "xgboost_version":
            xgboost.__version__,

        "sklearn_version":
            sklearn.__version__,

        "model_sha256":
            xgb_sha256,
    }

    with RF_METADATA_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            rf_metadata,
            file,
            indent=2,
        )

    with XGB_METADATA_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            xgb_metadata,
            file,
            indent=2,
        )

    return {
        "train":
            train,

        "validation":
            validation,

        "cv_fold_report":
            cv_fold_report,

        "rf_search":
            rf_search,

        "xgb_search":
            xgb_search,

        "best_rf":
            best_rf,

        "best_xgb":
            best_xgb,

        "rf_cv_summary":
            rf_cv_summary,

        "xgb_cv_summary":
            xgb_cv_summary,

        "rf_evaluation":
            rf_evaluation,

        "xgb_evaluation":
            xgb_evaluation,

        "rf_feature_importance":
            rf_feature_importance,

        "xgb_feature_importance":
            xgb_feature_importance,

        "comparison":
            comparison,

        "rf_search_seconds":
            rf_search_seconds,

        "xgb_search_seconds":
            xgb_search_seconds,
    }