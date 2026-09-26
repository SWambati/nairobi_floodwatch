import hashlib
import json
import time
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
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
from config.paths import PROCESSED_DIR
from src.model.model_dataset import (PREDICTOR_COLUMNS)


# Configuration
RANDOM_SEED = 42
BASELINE_THRESHOLD = 0.50
RF_BASELINE_PARAMS = {
    "n_estimators": 300,
    "random_state": RANDOM_SEED,
    "n_jobs": -1,
    "class_weight": None,
}


# Path configuration
PROJECT_OUTPUT_DIR = (PROCESSED_DIR.parent)
FEATURES_DIR = (PROJECT_OUTPUT_DIR/ "features")
MODEL_SPLIT_DIR = (FEATURES_DIR/ "modeling"/ "splits")
TRAIN_FILE = (MODEL_SPLIT_DIR/ "train_model_features.csv")
VALIDATION_FILE = (MODEL_SPLIT_DIR/ "validation_model_features.csv")
MODEL_ARTIFACT_DIR = (PROJECT_OUTPUT_DIR / "models"/ "random_forest"/ "baseline")
MODEL_REPORT_DIR = (PROCESSED_DIR / "modeling" / "random_forest" / "baseline")
MODEL_FILE = (MODEL_ARTIFACT_DIR/ "random_forest_baseline.joblib")
MODEL_METADATA_FILE = (MODEL_ARTIFACT_DIR/ "random_forest_baseline_metadata.json")
METRICS_FILE = (MODEL_REPORT_DIR/ "random_forest_baseline_metrics.csv")
CONFUSION_MATRIX_FILE = (MODEL_REPORT_DIR/ "random_forest_validation_confusion_matrix.csv")
FEATURE_IMPORTANCE_FILE = (MODEL_REPORT_DIR/ "random_forest_baseline_feature_importance.csv")
VALIDATION_PREDICTIONS_FILE = (MODEL_REPORT_DIR/ "random_forest_validation_predictions.csv")


# Generic helpers
def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], dataset_name: str) -> None:
    missing = (required_columns - set(dataframe.columns))

    if missing:
        raise ValueError(f"{dataset_name} is missing required columns: {sorted(missing)}")


def _validate_binary_labels(dataframe: pd.DataFrame, dataset_name: str) -> None:
    labels = set(dataframe["flood_label"].unique())

    if not labels.issubset({0, 1}):
        raise ValueError(f"{dataset_name} flood_label must contain only 0 and 1.")

    if labels != {0, 1,}:
        raise ValueError(f"{dataset_name} must contain both classes.")


# Dataset loading
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
            missing = (dataframe[PREDICTOR_COLUMNS].isna().sum())
            missing = (missing[missing > 0])
            raise ValueError(f"{dataset_name} contains missing predictor values:\n {missing}")

        predictor_values = (dataframe[PREDICTOR_COLUMNS].to_numpy(dtype=float))

        if not np.isfinite(predictor_values).all():
            raise ValueError(f"{dataset_name} contains non-finite predictor values.")


    # Final leakage guard:train and validation must not share split groups.    
    train_groups = set(train["model_split_group_id"])
    validation_groups = set(validation["model_split_group_id"])

    group_overlap = (train_groups & validation_groups)

    if group_overlap:
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
        "recall": recall_score(y_true, predictions, zero_division=0),
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
def build_feature_importance(model: RandomForestClassifier) -> pd.DataFrame:
    importance = pd.DataFrame(
        {
            "feature_name": PREDICTOR_COLUMNS,
            "importance": model.feature_importances_,
        }
    )

    importance = (importance.sort_values(by="importance", ascending=False).reset_index(drop=True))
    importance["rank"] = (np.arange(1, len(importance) + 1))
    importance["importance_sum"] = importance["importance"].sum()

    return importance[["rank", "feature_name", "importance", "importance_sum"]]


# Model checksum
def calculate_file_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024),b"",):
            sha256.update(block)

    return sha256.hexdigest()


# Main training routine
def train_random_forest_baseline():

    (train, validation) = load_model_splits()
    (x_train, y_train) = extract_xy(train)
    (x_validation, y_validation) = extract_xy(validation)

    model = RandomForestClassifier(**RF_BASELINE_PARAMS)


    # Training
    training_start = (time.perf_counter())
    model.fit(x_train, y_train)
    training_seconds = (time.perf_counter() - training_start)

    
    
    # Used training-set predictions to check for overfitting.
    train_probabilities = (model.predict_proba(x_train)[:, 1])

    (train_metrics, train_predictions) = calculate_metrics(
        split_name="train",
        y_true=y_train,
        probabilities=(train_probabilities),
        threshold=(BASELINE_THRESHOLD),
    )

    
    # Prediction validation
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

    joblib.dump(model, MODEL_FILE,)

    model_sha256 = (calculate_file_sha256(MODEL_FILE))

    metadata = {
        "model_name": "random_forest_baseline",
        "model_type":"RandomForestClassifier",
        "model_role": "primary_baseline_classifier",
        "predictor_count": len(PREDICTOR_COLUMNS),
        "predictor_columns": PREDICTOR_COLUMNS,
        "training_rows": len(train),
        "validation_rows": len(validation),
        "test_rows_used": 0,
        "decision_threshold": BASELINE_THRESHOLD,
        "class_weight": None,
        "resampling": None,
        "random_seed": RANDOM_SEED,
        "hyperparameters": RF_BASELINE_PARAMS,
        "training_seconds": training_seconds,
        "validation_inference_seconds": validation_inference_seconds,
        "validation_inference_ms_per_row": inference_ms_per_row,
        "sklearn_version": sklearn.__version__,
        "model_sha256": model_sha256,
    }

    with MODEL_METADATA_FILE.open("w", encoding="utf-8",) as file:
        json.dump(metadata, file, indent=2)

    metrics.to_csv(METRICS_FILE, index=False)
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