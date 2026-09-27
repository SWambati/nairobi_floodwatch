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
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from xgboost import XGBClassifier
from config.paths import PROCESSED_DIR
from src.model.model_dataset import PREDICTOR_COLUMNS


# Configuration
RANDOM_SEED = 42
DECISION_THRESHOLD = 0.50

# Locked before test evaluation based on Task 23 validation results.
FINAL_MODEL_NAME = "random_forest"


# Paths
PROJECT_OUTPUT_DIR = PROCESSED_DIR.parent
SPLIT_DIR = PROJECT_OUTPUT_DIR / "features" / "modeling" / "splits"
TUNING_DIR = PROCESSED_DIR / "modeling" / "tuning"
COMPARISON_DIR = PROCESSED_DIR / "modeling" / "comparison"
FINAL_REPORT_DIR = PROCESSED_DIR / "modeling" / "final_evaluation"
TRAIN_FILE = SPLIT_DIR / "train_model_features.csv"
VALIDATION_FILE = SPLIT_DIR / "validation_model_features.csv"
TEST_FILE = SPLIT_DIR / "test_model_features.csv"
BEST_PARAMS_FILE = TUNING_DIR / "best_hyperparameters.json"
VALIDATION_COMPARISON_FILE = COMPARISON_DIR / "comparative_evaluation_report.json"
FINAL_MODEL_DIR = PROJECT_OUTPUT_DIR / "models" / "final"
FINAL_RF_MODEL_FILE = FINAL_MODEL_DIR / "random_forest_final.joblib"
FINAL_XGB_COMPARISON_FILE = FINAL_MODEL_DIR / "xgboost_final_comparison.json"
FINAL_MODEL_METADATA_FILE = FINAL_MODEL_DIR / "final_model_metadata.json"
FINAL_TEST_METRICS_FILE = FINAL_REPORT_DIR / "final_test_metrics.csv"
FINAL_TEST_PREDICTIONS_FILE = FINAL_REPORT_DIR / "final_test_predictions.csv"
RF_CONFUSION_MATRIX_FILE = FINAL_REPORT_DIR / "random_forest_test_confusion_matrix.csv"
XGB_CONFUSION_MATRIX_FILE = FINAL_REPORT_DIR / "xgboost_test_confusion_matrix.csv"
RF_FEATURE_IMPORTANCE_FILE = FINAL_REPORT_DIR / "random_forest_final_feature_importance.csv"
FINAL_COMPARISON_FILE = FINAL_REPORT_DIR / "final_test_comparison.json"


# Generic Helpers
def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], dataset_name: str) -> None:
    missing = required_columns - set(dataframe.columns)

    if missing:
        raise ValueError(f"{dataset_name} is missing required columns: {sorted(missing)}")


def calculate_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(block)

    return sha256.hexdigest()


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    
    if isinstance(value, np.integer):
        return int(value)
    
    if isinstance(value, np.floating):
        return float(value)
    
    if isinstance(value, np.bool_):
        return bool(value)
    
    return value


# Dataset loading and integrity validation
def load_final_datasets() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(TRAIN_FILE)
    validation = pd.read_csv(VALIDATION_FILE)
    test = pd.read_csv(TEST_FILE)

    required_columns = {
        "model_record_id",
        "model_split_group_id",
        "flood_label",
        *PREDICTOR_COLUMNS,
    }

    datasets = {
        "training": train,
        "validation": validation,
        "test": test,
    }

    for dataset_name, dataframe in datasets.items():
        _require_columns(dataframe, required_columns, f"{dataset_name.capitalize()} dataset")

        if dataframe["model_record_id"].duplicated().any():
            raise ValueError(f"{dataset_name.capitalize()} dataset contains duplicate model IDs.")

        if set(dataframe["flood_label"].unique()) != {0, 1}:
            raise ValueError(f"{dataset_name.capitalize()} dataset must contain both classes.")

        for column in PREDICTOR_COLUMNS:
            dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")

        if dataframe[PREDICTOR_COLUMNS].isna().any().any():
            raise ValueError(f"{dataset_name.capitalize()} dataset contains missing predictors.")

        values = dataframe[PREDICTOR_COLUMNS].to_numpy(dtype=float)

        if not np.isfinite(values).all():
            raise ValueError(f"{dataset_name.capitalize()} dataset contains non-finite predictors.")

    # Model-record leakage checks
    train_ids = set(train["model_record_id"])
    validation_ids = set(validation["model_record_id"])
    test_ids = set(test["model_record_id"])

    if train_ids & validation_ids:
        raise ValueError("Training-validation model-record leakage detected.")
    if train_ids & test_ids:
        raise ValueError("Training-test model-record leakage detected.")
    if validation_ids & test_ids:
        raise ValueError("Validation-test model-record leakage detected.")

    # Event-group leakage checks
    train_groups = set(train["model_split_group_id"])
    validation_groups = set(validation["model_split_group_id"])
    test_groups = set(test["model_split_group_id"])

    if train_groups & validation_groups:
        raise ValueError("Training-validation event-group leakage detected.")
    
    if train_groups & test_groups:
        raise ValueError("Training-test event-group leakage detected.")
    
    if validation_groups & test_groups:
        raise ValueError("Validation-test event-group leakage detected.")

    return train, validation, test


# Locked hyperparameters
def load_best_parameters() -> tuple[dict, dict]:

    if not BEST_PARAMS_FILE.exists():
        raise FileNotFoundError(f"Best hyperparameter file not found: {BEST_PARAMS_FILE}")

    with BEST_PARAMS_FILE.open("r", encoding="utf-8") as file:
        parameters = json.load(file)

    if "random_forest" not in parameters or "xgboost" not in parameters:
        raise ValueError("Best hyperparameter file does not contain both model configurations.")

    return parameters["random_forest"], parameters["xgboost"]


# Model builders
def build_random_forest(parameters: dict) -> RandomForestClassifier:
    return RandomForestClassifier(
        **parameters,
        random_state=RANDOM_SEED,
        class_weight=None,
        n_jobs=-1,
    )


def build_xgboost(parameters: dict) -> XGBClassifier:
    return XGBClassifier(
        **parameters,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        scale_pos_weight=1.0,
        random_state=RANDOM_SEED,
        n_jobs=-1,
        verbosity=0,
    )


# Metrics
def calculate_metrics(model_name: str, y_true: pd.Series, probabilities: np.ndarray) -> tuple[dict, np.ndarray, pd.DataFrame]:
    predictions = (probabilities >= DECISION_THRESHOLD).astype(int)
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1])
    tn, fp, fn, tp = matrix.ravel()

    precision_curve, recall_curve, _ = precision_recall_curve(y_true, probabilities)

    metrics = {
        "model": model_name,
        "split": "test",
        "rows": len(y_true),
        "threshold": DECISION_THRESHOLD,
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

    confusion = pd.DataFrame(
        matrix,
        index=["actual_non_flood", "actual_flood"],
        columns=["predicted_non_flood", "predicted_flood"],
    )

    return metrics, predictions, confusion


# Final RF feature importance
def build_rf_feature_importance(model: RandomForestClassifier) -> pd.DataFrame:
    result = pd.DataFrame({
        "feature_name": PREDICTOR_COLUMNS,
        "importance": model.feature_importances_,
    })

    result = result.sort_values(by="importance", ascending=False).reset_index(drop=True)
    result["rank"] = np.arange(1, len(result) + 1)

    return result[["rank", "feature_name", "importance"]]


# Final model fitting and test evaluation
def finalize_models():
    if FINAL_MODEL_NAME != "random_forest":
        raise ValueError("Final model selection lock has unexpectedly changed.")

    train, validation, test = load_final_datasets()
    rf_parameters, xgb_parameters = load_best_parameters()

    development = pd.concat([train, validation], ignore_index=True)

    if development["model_record_id"].duplicated().any():
        raise ValueError("Combined development dataset contains duplicate model IDs.")

    x_development = development[PREDICTOR_COLUMNS]
    y_development = development["flood_label"].astype(int)

    x_test = test[PREDICTOR_COLUMNS]
    y_test = test["flood_label"].astype(int)

    rf_model = build_random_forest(rf_parameters)
    xgb_model = build_xgboost(xgb_parameters)

    # Refit locked configurations on all development data
    rf_training_start = time.perf_counter()
    rf_model.fit(x_development, y_development)
    rf_training_seconds = time.perf_counter() - rf_training_start

    xgb_training_start = time.perf_counter()
    xgb_model.fit(x_development, y_development)
    xgb_training_seconds = time.perf_counter() - xgb_training_start

    # Held-out test evaluation
    rf_inference_start = time.perf_counter()
    rf_probabilities = rf_model.predict_proba(x_test)[:, 1]
    rf_inference_seconds = time.perf_counter() - rf_inference_start

    xgb_inference_start = time.perf_counter()
    xgb_probabilities = xgb_model.predict_proba(x_test)[:, 1]
    xgb_inference_seconds = time.perf_counter() - xgb_inference_start

    rf_metrics, rf_predictions, rf_confusion = calculate_metrics("random_forest", y_test, rf_probabilities)
    xgb_metrics, xgb_predictions, xgb_confusion = calculate_metrics("xgboost", y_test, xgb_probabilities)

    rf_metrics["training_seconds"] = rf_training_seconds
    rf_metrics["test_inference_seconds"] = rf_inference_seconds
    rf_metrics["test_inference_ms_per_row"] = rf_inference_seconds / len(test) * 1000

    xgb_metrics["training_seconds"] = xgb_training_seconds
    xgb_metrics["test_inference_seconds"] = xgb_inference_seconds
    xgb_metrics["test_inference_ms_per_row"] = xgb_inference_seconds / len(test) * 1000

    metrics = pd.DataFrame([rf_metrics, xgb_metrics])

    predictions = test[["model_record_id", "model_split_group_id", "flood_label"]].copy()

    predictions["rf_probability"] = rf_probabilities
    predictions["rf_predicted_label"] = rf_predictions
    predictions["rf_correct"] = predictions["rf_predicted_label"] == predictions["flood_label"]

    predictions["xgb_probability"] = xgb_probabilities
    predictions["xgb_predicted_label"] = xgb_predictions
    predictions["xgb_correct"] = predictions["xgb_predicted_label"] == predictions["flood_label"]

    predictions["prediction_agreement"] = (predictions["rf_predicted_label"] == predictions["xgb_predicted_label"])

    rf_feature_importance = build_rf_feature_importance(rf_model)

    # Output directories
    FINAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    FINAL_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Persist production RF and comparison XGBoost
    joblib.dump(rf_model, FINAL_RF_MODEL_FILE)
    xgb_model.save_model(str(FINAL_XGB_COMPARISON_FILE))

    rf_sha256 = calculate_sha256(FINAL_RF_MODEL_FILE)
    xgb_sha256 = calculate_sha256(FINAL_XGB_COMPARISON_FILE)

    metrics.to_csv(FINAL_TEST_METRICS_FILE, index=False)
    predictions.to_csv(FINAL_TEST_PREDICTIONS_FILE, index=False)
    rf_confusion.to_csv(RF_CONFUSION_MATRIX_FILE)
    xgb_confusion.to_csv(XGB_CONFUSION_MATRIX_FILE)
    rf_feature_importance.to_csv(RF_FEATURE_IMPORTANCE_FILE, index=False)

    test_group_count = test["model_split_group_id"].nunique()

    comparison_report = {
        "evaluation_stage": "final_held_out_test",
        "selection_locked_before_test": True,
        "selected_production_model": FINAL_MODEL_NAME,
        "selection_basis": (
            "Random Forest was selected before test evaluation based on Task 23 validation results: stronger overall F1, accuracy, precision, ROC-AUC and PR-AUC. "
            "RF-XGBoost ensemble development was skipped because validation predictions showed insufficient complementarity."
        ),
        "development_rows": len(development),
        "development_flood_rows": int((y_development == 1).sum()),
        "development_non_flood_rows": int((y_development == 0).sum()),
        "test_rows": len(test),
        "test_event_groups": int(test_group_count),
        "test_flood_rows": int((y_test == 1).sum()),
        "test_non_flood_rows": int((y_test == 0).sum()),
        "decision_threshold": DECISION_THRESHOLD,
        "random_forest_test_metrics": rf_metrics,
        "xgboost_test_metrics": xgb_metrics,
        "descriptive_rf_minus_xgb": {
            "accuracy": rf_metrics["accuracy"] - xgb_metrics["accuracy"],
            "precision": rf_metrics["precision"] - xgb_metrics["precision"],
            "recall": rf_metrics["recall"] - xgb_metrics["recall"],
            "f1": rf_metrics["f1"] - xgb_metrics["f1"],
            "roc_auc": rf_metrics["roc_auc"] - xgb_metrics["roc_auc"],
            "pr_auc": rf_metrics["pr_auc"] - xgb_metrics["pr_auc"],
        },
        "test_prediction_agreement_rate": float(predictions["prediction_agreement"].mean()),
        "interpretation_note": (
            "The held-out test set was used only after model development decisions were completed. "
            "Test performance is reported for final unbiased evaluation and was not used to change the pre-test model selection."
        ),
    }

    with FINAL_COMPARISON_FILE.open("w", encoding="utf-8") as file:
        json.dump(_json_safe(comparison_report), file, indent=2)

    final_metadata = {
        "model_name": "nairobi_floodwatch_random_forest",
        "model_type": "RandomForestClassifier",
        "model_role": "final_production_classifier",
        "selection_locked_before_test": True,
        "selection_source": "Task 23 validation comparative evaluation",
        "ensemble_used": False,
        "ensemble_reason": "Insufficient RF-XGBoost validation prediction complementarity.",
        "predictor_count": len(PREDICTOR_COLUMNS),
        "predictor_columns": PREDICTOR_COLUMNS,
        "development_rows": len(development),
        "test_rows": len(test),
        "test_event_groups": int(test_group_count),
        "decision_threshold": DECISION_THRESHOLD,
        "class_weight": None,
        "resampling": None,
        "hyperparameters": rf_parameters,
        "test_metrics": rf_metrics,
        "random_seed": RANDOM_SEED,
        "sklearn_version": sklearn.__version__,
        "xgboost_version": xgboost.__version__,
        "production_model_sha256": rf_sha256,
        "comparison_model_sha256": xgb_sha256,
    }

    with FINAL_MODEL_METADATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(_json_safe(final_metadata), file, indent=2)

    return {
        "train": train,
        "validation": validation,
        "development": development,
        "test": test,
        "rf_model": rf_model,
        "xgb_model": xgb_model,
        "metrics": metrics,
        "predictions": predictions,
        "rf_confusion_matrix": rf_confusion,
        "xgb_confusion_matrix": xgb_confusion,
        "rf_feature_importance": rf_feature_importance,
        "rf_training_seconds": rf_training_seconds,
        "xgb_training_seconds": xgb_training_seconds,
        "rf_sha256": rf_sha256,
        "xgb_sha256": xgb_sha256,
    }