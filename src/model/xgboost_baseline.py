import hashlib
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.metrics import (
    accuracy_score,
    auc,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from xgboost import XGBClassifier
from config.paths import PROCESSED_DIR
from src.model.model_dataset import (PREDICTOR_COLUMNS)


# Configuration
RANDOM_SEED = 42
BASELINE_THRESHOLD = 0.50
XGBOOST_BASELINE_PARAMS = {
    "objective": "binary:logistic",
    "n_estimators": 100,
    "learning_rate": 0.30,
    "max_depth": 6,
    "min_child_weight": 1,
    "gamma": 0.0,
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "reg_alpha": 0.0,
    "reg_lambda": 1.0,
    "scale_pos_weight": 1.0,
    "eval_metric": "logloss",
    "tree_method": "hist",
    "random_state": RANDOM_SEED,
    "n_jobs": -1,
    "verbosity": 0,
}


# Path Configuration
PROJECT_OUTPUT_DIR = (PROCESSED_DIR.parent)
FEATURES_DIR = (PROJECT_OUTPUT_DIR/ "features")
MODEL_SPLIT_DIR = (FEATURES_DIR/ "modeling"/ "splits")
TRAIN_FILE = (MODEL_SPLIT_DIR/ "train_model_features.csv")
VALIDATION_FILE = (MODEL_SPLIT_DIR/ "validation_model_features.csv")
MODEL_ARTIFACT_DIR = (PROJECT_OUTPUT_DIR/ "models"/ "xgboost"/ "baseline")
MODEL_REPORT_DIR = (PROCESSED_DIR/ "modeling"/ "xgboost"/ "baseline")
MODEL_FILE = (MODEL_ARTIFACT_DIR/ "xgboost_baseline.json")
MODEL_METADATA_FILE = (MODEL_ARTIFACT_DIR/ "xgboost_baseline_metadata.json")
METRICS_FILE = (MODEL_REPORT_DIR/ "xgboost_baseline_metrics.csv")
CONFUSION_MATRIX_FILE = (MODEL_REPORT_DIR/ "xgboost_validation_confusion_matrix.csv")
FEATURE_IMPORTANCE_FILE = (MODEL_REPORT_DIR/ "xgboost_baseline_feature_importance.csv")
VALIDATION_PREDICTIONS_FILE = (MODEL_REPORT_DIR/ "xgboost_validation_predictions.csv")


# Generic helpers
def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], dataset_name: str) -> None:
    missing = (required_columns - set(dataframe.columns))

    if missing:
        raise ValueError(f"{dataset_name} is missing required columns: {sorted(missing)}")

def _validate_binary_labels(dataframe: pd.DataFrame, dataset_name: str) -> None:
    labels = set(dataframe["flood_label"].unique())

    if not labels.issubset({0, 1}):
        raise ValueError(f"{dataset_name} flood_label must contain only 0 and 1.")

    if labels != {0, 1}:
        raise ValueError(f"{dataset_name} must contain both classes.")


# Load dataset
def load_model_splits() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(TRAIN_FILE)
    validation = pd.read_csv(VALIDATION_FILE)

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

    _validate_binary_labels(
        train,
        "Training dataset",
    )

    _validate_binary_labels(
        validation,
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

        if dataframe["model_record_id"].duplicated().any():
            raise ValueError(f"{dataset_name} contains duplicate model IDs.")

        for column in (PREDICTOR_COLUMNS):
            dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")

        if dataframe[PREDICTOR_COLUMNS].isna().any().any():
            missing = dataframe[PREDICTOR_COLUMNS].isna().sum()
            missing = (missing[missing > 0])
            raise ValueError(f"{dataset_name} contains missing predictor values:\n {missing}")

        predictor_values = (dataframe[PREDICTOR_COLUMNS].to_numpy(dtype=float))

        if not np.isfinite(predictor_values).all():
            raise ValueError(f"{dataset_name} contains non-finite predictor values.")

    
    # Final leakage guard
    train_groups = set(train["model_split_group_id"])
    validation_groups = set(validation["model_split_group_id"])

    overlap = (train_groups & validation_groups)
    if overlap:
        raise ValueError("Training and validation split-group leakage detected.")

    return (train, validation)


# Feature extraction
def extract_xy(dataframe: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    x = dataframe[PREDICTOR_COLUMNS].copy()
    y = dataframe["flood_label"].astype(int)

    return (x, y)


# Metric calculation
def calculate_metrics(split_name: str, y_true: pd.Series, probabilities: np.ndarray, threshold: float) -> tuple[dict, np.ndarray]:
    predictions = (probabilities >= threshold).astype(int)

    matrix = confusion_matrix(y_true, predictions, labels=[0, 1],)

    tn, fp, fn, tp = (matrix.ravel())

    (precision_curve, recall_curve, _,) = precision_recall_curve(y_true, probabilities)

    metrics = {
        "split": split_name,
        "threshold": threshold,
        "rows": len(y_true),
        "accuracy": accuracy_score(y_true, predictions),
        "precision": precision_score(y_true, predictions, zero_division=0),
        "recall":  recall_score(y_true, predictions, zero_division=0),
        "f1": f1_score(y_true, predictions, zero_division=0),
        "roc_auc": roc_auc_score(y_true, probabilities),
        "pr_auc": auc(recall_curve, precision_curve),
        "average_precision": average_precision_score(y_true, probabilities),
        "true_negative": int(tn),
        "false_positive": int(fp),
        "false_negative": int(fn),
        "true_positive": int(tp),
    }

    return (metrics, predictions)


# Feature importance
def build_feature_importance(model: XGBClassifier) -> pd.DataFrame:
    booster = (model.get_booster())
    gain_scores = (booster.get_score(importance_type="gain"))

    rows = []

    for feature in (PREDICTOR_COLUMNS):
        rows.append(
            {
                "feature_name": feature,
                "gain": float(gain_scores.get(feature, 0.0)),
            }
        )

    importance = pd.DataFrame(rows)

    total_gain = (importance["gain"].sum())

    if total_gain > 0:
        importance["importance"] = (importance["gain"] / total_gain)

    else:
        importance["importance"] = 0.0

    importance = (importance.sort_values(by="importance", ascending=False).reset_index(drop=True))
    importance["rank"] = np.arange(1, len(importance) + 1)

    return importance[
        [
            "rank",
            "feature_name",
            "gain",
            "importance",
        ]
    ]


# Model checksum
def calculate_file_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b"",):
            sha256.update(block)

    return sha256.hexdigest()


# Main baseline training routine
def train_xgboost_baseline():

    (train, validation) = load_model_splits()
    (x_train, y_train) = extract_xy(train)
    (x_validation, y_validation) = extract_xy(validation)

    model = XGBClassifier(**XGBOOST_BASELINE_PARAMS)

   
    # Training
    # Deliberately train on TRAIN only. No early stopping and no validation-base model selection at baseline stage.
    training_start = (time.perf_counter())
    model.fit(x_train, y_train)

    training_seconds = (time.perf_counter() - training_start)

   
    # Training-set prediction to test for overfitting
    train_probabilities = (model.predict_proba(x_train)[:, 1])

    (train_metrics, train_predictions) = calculate_metrics(
        split_name="train",
        y_true=y_train,
        probabilities=(train_probabilities),
        threshold=(BASELINE_THRESHOLD),
    )

    
    # Validation-set prediction
    inference_start = (time.perf_counter())
    validation_probabilities = (model.predict_proba(x_validation)[:, 1])
    validation_inference_seconds = (time.perf_counter() - inference_start)

    (validation_metrics, validation_predictions) = calculate_metrics(
        split_name="validation",
        y_true=y_validation,
        probabilities=(validation_probabilities),
        threshold=(BASELINE_THRESHOLD),
    )

    inference_ms_per_row = (validation_inference_seconds / len(x_validation) * 1000)

    # Reports
    metrics = pd.DataFrame([train_metrics, validation_metrics])
    metrics["training_seconds"] = training_seconds
    metrics["validation_inference_seconds"] = (validation_inference_seconds)
    metrics["validation_inference_ms_per_row"] = (inference_ms_per_row)

    feature_importance = (build_feature_importance(model))

    validation_matrix = (confusion_matrix(y_validation, validation_predictions, labels=[0, 1],))

    confusion_df = pd.DataFrame(
        validation_matrix,
        index=[
            "actual_non_flood",
            "actual_flood",
        ],
        columns=[
            "predicted_non_flood",
            "predicted_flood",
        ],
    )

    validation_predictions_df = (
        validation[
            [
                "model_record_id",
                "model_split_group_id",
                "flood_label",
            ]
        ]
        .copy()
    )

    validation_predictions_df["flood_probability"] = (validation_probabilities)
    validation_predictions_df["predicted_label"] = (validation_predictions)
    validation_predictions_df["correct_prediction"] = (validation_predictions_df["flood_label"] == validation_predictions_df["predicted_label"])

   
    # Save model and reports
    MODEL_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Native XGBoost JSON artifact.
    model.save_model(MODEL_FILE)
    model_sha256 = (calculate_file_sha256(MODEL_FILE))

    metadata = {
        "model_name": "xgboost_baseline",
        "model_type": "XGBClassifier",
        "model_role": "comparison_baseline_classifier",
        "predictor_count": len(PREDICTOR_COLUMNS),
        "predictor_columns": PREDICTOR_COLUMNS,
        "training_rows": len(train),
        "validation_rows": len(validation),
        "test_rows_used": 0,
        "decision_threshold": BASELINE_THRESHOLD,
        "scale_pos_weight": 1.0,
        "class_weighting": None,
        "resampling": None,
        "early_stopping": False,
        "random_seed": RANDOM_SEED,
        "hyperparameters": XGBOOST_BASELINE_PARAMS,
        "training_seconds": training_seconds,
        "validation_inference_seconds": validation_inference_seconds,
        "validation_inference_ms_per_row": inference_ms_per_row,
        "xgboost_version": xgboost.__version__,
        "sklearn_version": sklearn.__version__,
        "model_sha256": model_sha256,
    }

    with MODEL_METADATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)

    metrics.to_csv(METRICS_FILE, index=False,)
    confusion_df.to_csv(CONFUSION_MATRIX_FILE)
    feature_importance.to_csv(FEATURE_IMPORTANCE_FILE, index=False)
    validation_predictions_df.to_csv(VALIDATION_PREDICTIONS_FILE, index=False)

    return {
        "model": model,
        "train": train,
        "validation": validation,
        "metrics": metrics,
        "feature_importance": feature_importance,
        "confusion_matrix": confusion_df,
        "validation_predictions": validation_predictions_df,
        "training_seconds": training_seconds,
        "validation_inference_seconds": validation_inference_seconds,
        "validation_inference_ms_per_row": inference_ms_per_row,
        "model_sha256": model_sha256,
    }