import json

import numpy as np
import pandas as pd

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


# Configuration
DECISION_THRESHOLD = 0.50
METRIC_TOLERANCE = 1e-6


# Paths
TUNING_DIR = PROCESSED_DIR / "modeling" / "tuning"
COMPARISON_DIR = PROCESSED_DIR / "modeling" / "comparison"

RF_PREDICTIONS_FILE = TUNING_DIR / "random_forest_tuned_validation_predictions.csv"
XGB_PREDICTIONS_FILE = TUNING_DIR / "xgboost_tuned_validation_predictions.csv"
TUNED_METRICS_SOURCE_FILE = TUNING_DIR / "tuned_model_metrics.csv"

RF_FEATURE_IMPORTANCE_FILE = TUNING_DIR / "random_forest_tuned_feature_importance.csv"
XGB_FEATURE_IMPORTANCE_FILE = TUNING_DIR / "xgboost_tuned_feature_importance.csv"

MODEL_METRICS_FILE = COMPARISON_DIR / "tuned_model_validation_metrics.csv"
PREDICTION_AGREEMENT_FILE = COMPARISON_DIR / "validation_prediction_agreement.csv"
DISAGREEMENT_CASES_FILE = COMPARISON_DIR / "validation_disagreement_cases.csv"
ERROR_OVERLAP_FILE = COMPARISON_DIR / "validation_error_overlap_summary.csv"
GROUP_ERROR_FILE = COMPARISON_DIR / "validation_group_error_analysis.csv"
FEATURE_RANK_FILE = COMPARISON_DIR / "feature_rank_comparison.csv"
COMPARISON_REPORT_FILE = COMPARISON_DIR / "comparative_evaluation_report.json"


# Helpers
def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], dataset_name: str) -> None:
    missing = required_columns - set(dataframe.columns)
    if missing:
        raise ValueError(f"{dataset_name} is missing required columns: {sorted(missing)}")


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


def _safe_ratio(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


# Prediction loading and integrity checks
def load_validation_predictions() -> pd.DataFrame:
    rf = pd.read_csv(RF_PREDICTIONS_FILE)
    xgb = pd.read_csv(XGB_PREDICTIONS_FILE)

    required_columns = {
        "model_record_id",
        "model_split_group_id",
        "flood_label",
        "flood_probability",
        "predicted_label",
        "correct_prediction",
    }

    _require_columns(rf, required_columns, "Random Forest validation predictions")
    _require_columns(xgb, required_columns, "XGBoost validation predictions")

    if rf["model_record_id"].duplicated().any():
        raise ValueError("Random Forest validation predictions contain duplicate model IDs.")

    if xgb["model_record_id"].duplicated().any():
        raise ValueError("XGBoost validation predictions contain duplicate model IDs.")

    rf = rf.rename(columns={
        "flood_probability": "rf_probability",
        "predicted_label": "rf_predicted_label",
        "correct_prediction": "rf_correct",
    })

    xgb = xgb.rename(columns={
        "flood_probability": "xgb_probability",
        "predicted_label": "xgb_predicted_label",
        "correct_prediction": "xgb_correct",
    })

    key_columns = ["model_record_id", "model_split_group_id", "flood_label"]

    merged = rf.merge(
        xgb,
        on=key_columns,
        how="outer",
        validate="one_to_one",
        indicator=True,
    )

    if not (merged["_merge"] == "both").all():
        raise ValueError("RF and XGBoost validation prediction records do not match exactly.")

    merged = merged.drop(columns="_merge")

    for column in ["flood_label", "rf_predicted_label", "xgb_predicted_label"]:
        merged[column] = pd.to_numeric(merged[column], errors="raise").astype(int)

    for column in ["rf_probability", "xgb_probability"]:
        merged[column] = pd.to_numeric(merged[column], errors="raise")

        if not np.isfinite(merged[column]).all():
            raise ValueError(f"{column} contains non-finite values.")

        if not merged[column].between(0.0, 1.0).all():
            raise ValueError(f"{column} contains probabilities outside [0, 1].")

    merged["rf_correct"] = merged["rf_predicted_label"] == merged["flood_label"]
    merged["xgb_correct"] = merged["xgb_predicted_label"] == merged["flood_label"]

    merged["prediction_agreement"] = (
        merged["rf_predicted_label"] == merged["xgb_predicted_label"]
    )

    merged["probability_difference"] = (
        merged["rf_probability"] - merged["xgb_probability"]
    )

    merged["absolute_probability_difference"] = merged["probability_difference"].abs()

    conditions = [
        merged["rf_correct"] & merged["xgb_correct"],
        merged["rf_correct"] & ~merged["xgb_correct"],
        ~merged["rf_correct"] & merged["xgb_correct"],
        ~merged["rf_correct"] & ~merged["xgb_correct"],
    ]

    outcomes = [
        "both_correct",
        "rf_only_correct",
        "xgb_only_correct",
        "both_wrong",
    ]

    merged["comparison_outcome"] = np.select(conditions, outcomes, default="unknown")

    return merged


# Metric calculation
def calculate_model_metrics(
    model_name: str,
    y_true: pd.Series,
    probabilities: pd.Series,
    predictions: pd.Series,
) -> dict:
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1])
    tn, fp, fn, tp = matrix.ravel()

    precision_curve, recall_curve, _ = precision_recall_curve(y_true, probabilities)

    return {
        "model": model_name,
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


def build_model_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    y_true = predictions["flood_label"]

    rf_metrics = calculate_model_metrics(
        "random_forest",
        y_true,
        predictions["rf_probability"],
        predictions["rf_predicted_label"],
    )

    xgb_metrics = calculate_model_metrics(
        "xgboost",
        y_true,
        predictions["xgb_probability"],
        predictions["xgb_predicted_label"],
    )

    return pd.DataFrame([rf_metrics, xgb_metrics])


# Verify Task 23 metrics reproduce Task 22
def verify_task22_metrics(metrics: pd.DataFrame) -> None:
    if not TUNED_METRICS_SOURCE_FILE.exists():
        raise FileNotFoundError(f"Task 22 metrics not found: {TUNED_METRICS_SOURCE_FILE}")

    source = pd.read_csv(TUNED_METRICS_SOURCE_FILE)
    source = source[source["split"] == "validation"].copy()

    fields = ["accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc"]

    for model_name in ["random_forest", "xgboost"]:
        task22_rows = source[source["model"] == model_name]
        task23_rows = metrics[metrics["model"] == model_name]

        if task22_rows.empty or task23_rows.empty:
            raise ValueError(f"Missing validation metrics for {model_name}.")

        task22 = task22_rows.iloc[0]
        task23 = task23_rows.iloc[0]

        for field in fields:
            difference = abs(float(task22[field]) - float(task23[field]))

            if difference > METRIC_TOLERANCE:
                raise ValueError(
                    f"{model_name} {field} differs from Task 22 by {difference:.8f}."
                )


# Error-overlap analysis
def build_error_overlap_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    total = len(predictions)

    both_correct = int((predictions["comparison_outcome"] == "both_correct").sum())
    rf_only_correct = int((predictions["comparison_outcome"] == "rf_only_correct").sum())
    xgb_only_correct = int((predictions["comparison_outcome"] == "xgb_only_correct").sum())
    both_wrong = int((predictions["comparison_outcome"] == "both_wrong").sum())

    agreement = int(predictions["prediction_agreement"].sum())
    disagreement = total - agreement

    actual_flood = predictions["flood_label"] == 1
    actual_non_flood = predictions["flood_label"] == 0

    rf_fn = actual_flood & (predictions["rf_predicted_label"] == 0)
    xgb_fn = actual_flood & (predictions["xgb_predicted_label"] == 0)

    rf_fp = actual_non_flood & (predictions["rf_predicted_label"] == 1)
    xgb_fp = actual_non_flood & (predictions["xgb_predicted_label"] == 1)

    common_fn = int((rf_fn & xgb_fn).sum())
    rf_only_fn = int((rf_fn & ~xgb_fn).sum())
    xgb_only_fn = int((xgb_fn & ~rf_fn).sum())

    common_fp = int((rf_fp & xgb_fp).sum())
    rf_only_fp = int((rf_fp & ~xgb_fp).sum())
    xgb_only_fp = int((xgb_fp & ~rf_fp).sum())

    fn_union = int((rf_fn | xgb_fn).sum())
    fp_union = int((rf_fp | xgb_fp).sum())

    probability_correlation = predictions[
        ["rf_probability", "xgb_probability"]
    ].corr().iloc[0, 1]

    mean_absolute_probability_difference = predictions[
        "absolute_probability_difference"
    ].mean()

    oracle_correct = int((predictions["rf_correct"] | predictions["xgb_correct"]).sum())

    summary = {
        "rows": total,
        "prediction_agreement_count": agreement,
        "prediction_agreement_rate": _safe_ratio(agreement, total),
        "prediction_disagreement_count": disagreement,
        "prediction_disagreement_rate": _safe_ratio(disagreement, total),
        "both_correct": both_correct,
        "rf_only_correct": rf_only_correct,
        "xgb_only_correct": xgb_only_correct,
        "both_wrong": both_wrong,
        "either_model_correct": oracle_correct,
        "oracle_upper_bound_accuracy": _safe_ratio(oracle_correct, total),
        "rf_false_negatives": int(rf_fn.sum()),
        "xgb_false_negatives": int(xgb_fn.sum()),
        "common_false_negatives": common_fn,
        "rf_only_false_negatives": rf_only_fn,
        "xgb_only_false_negatives": xgb_only_fn,
        "false_negative_jaccard": _safe_ratio(common_fn, fn_union),
        "rf_false_positives": int(rf_fp.sum()),
        "xgb_false_positives": int(xgb_fp.sum()),
        "common_false_positives": common_fp,
        "rf_only_false_positives": rf_only_fp,
        "xgb_only_false_positives": xgb_only_fp,
        "false_positive_jaccard": _safe_ratio(common_fp, fp_union),
        "probability_correlation": float(probability_correlation),
        "mean_absolute_probability_difference": float(mean_absolute_probability_difference),
    }

    return pd.DataFrame([summary])


# Event-group error analysis
def build_group_error_analysis(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for group_id, group in predictions.groupby("model_split_group_id"):
        actual_flood = group["flood_label"] == 1
        actual_non_flood = group["flood_label"] == 0

        rf_fn = actual_flood & (group["rf_predicted_label"] == 0)
        xgb_fn = actual_flood & (group["xgb_predicted_label"] == 0)

        rf_fp = actual_non_flood & (group["rf_predicted_label"] == 1)
        xgb_fp = actual_non_flood & (group["xgb_predicted_label"] == 1)

        rows.append({
            "model_split_group_id": group_id,
            "rows": len(group),
            "flood_rows": int(actual_flood.sum()),
            "non_flood_rows": int(actual_non_flood.sum()),
            "rf_correct": int(group["rf_correct"].sum()),
            "xgb_correct": int(group["xgb_correct"].sum()),
            "rf_errors": int((~group["rf_correct"]).sum()),
            "xgb_errors": int((~group["xgb_correct"]).sum()),
            "rf_accuracy": float(group["rf_correct"].mean()),
            "xgb_accuracy": float(group["xgb_correct"].mean()),
            "rf_false_negatives": int(rf_fn.sum()),
            "xgb_false_negatives": int(xgb_fn.sum()),
            "rf_false_positives": int(rf_fp.sum()),
            "xgb_false_positives": int(xgb_fp.sum()),
            "prediction_disagreements": int((~group["prediction_agreement"]).sum()),
            "both_wrong": int((group["comparison_outcome"] == "both_wrong").sum()),
            "rf_only_correct": int((group["comparison_outcome"] == "rf_only_correct").sum()),
            "xgb_only_correct": int((group["comparison_outcome"] == "xgb_only_correct").sum()),
        })

    result = pd.DataFrame(rows)

    return result.sort_values(
        by=["both_wrong", "prediction_disagreements", "rows"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


# Feature-rank comparison
def build_feature_rank_comparison() -> tuple[pd.DataFrame, dict]:
    rf = pd.read_csv(RF_FEATURE_IMPORTANCE_FILE)
    xgb = pd.read_csv(XGB_FEATURE_IMPORTANCE_FILE)

    _require_columns(rf, {"rank", "feature_name", "importance"}, "RF feature importance")
    _require_columns(xgb, {"rank", "feature_name", "importance"}, "XGBoost feature importance")

    rf = rf[["feature_name", "rank", "importance"]].rename(columns={
        "rank": "rf_rank",
        "importance": "rf_importance",
    })

    xgb = xgb[["feature_name", "rank", "importance"]].rename(columns={
        "rank": "xgb_rank",
        "importance": "xgb_gain_importance",
    })

    comparison = rf.merge(xgb, on="feature_name", how="outer", validate="one_to-one")
    comparison["absolute_rank_difference"] = (
        comparison["rf_rank"] - comparison["xgb_rank"]
    ).abs()

    comparison = comparison.sort_values(
        by=["rf_rank", "xgb_rank"]
    ).reset_index(drop=True)

    top_5_rf = set(comparison.nsmallest(5, "rf_rank")["feature_name"])
    top_5_xgb = set(comparison.nsmallest(5, "xgb_rank")["feature_name"])

    summary = {
        "top_5_rf_features": sorted(top_5_rf),
        "top_5_xgb_features": sorted(top_5_xgb),
        "top_5_feature_overlap": sorted(top_5_rf & top_5_xgb),
        "top_5_overlap_count": len(top_5_rf & top_5_xgb),
    }

    return comparison, summary


# Main comparative evaluation
def compare_tuned_models():
    predictions = load_validation_predictions()
    metrics = build_model_metrics(predictions)

    verify_task22_metrics(metrics)

    error_overlap = build_error_overlap_summary(predictions)
    group_errors = build_group_error_analysis(predictions)
    feature_ranks, feature_summary = build_feature_rank_comparison()

    disagreements = predictions[
        ~predictions["prediction_agreement"]
    ].copy()

    disagreements = disagreements.sort_values(
        by="absolute_probability_difference",
        ascending=False,
    ).reset_index(drop=True)

    rf_metrics = metrics[metrics["model"] == "random_forest"].iloc[0]
    xgb_metrics = metrics[metrics["model"] == "xgboost"].iloc[0]
    overlap = error_overlap.iloc[0]

    report = {
        "evaluation_stage": "validation_comparative_evaluation",
        "test_rows_used": 0,
        "decision_threshold": DECISION_THRESHOLD,
        "validation_rows": len(predictions),
        "validation_event_groups": predictions["model_split_group_id"].nunique(),
        "task22_metric_reproduction": "PASS",
        "random_forest": {
            "accuracy": rf_metrics["accuracy"],
            "precision": rf_metrics["precision"],
            "recall": rf_metrics["recall"],
            "f1": rf_metrics["f1"],
            "roc_auc": rf_metrics["roc_auc"],
            "pr_auc": rf_metrics["pr_auc"],
        },
        "xgboost": {
            "accuracy": xgb_metrics["accuracy"],
            "precision": xgb_metrics["precision"],
            "recall": xgb_metrics["recall"],
            "f1": xgb_metrics["f1"],
            "roc_auc": xgb_metrics["roc_auc"],
            "pr_auc": xgb_metrics["pr_auc"],
        },
        "descriptive_metric_differences_rf_minus_xgb": {
            "accuracy": rf_metrics["accuracy"] - xgb_metrics["accuracy"],
            "precision": rf_metrics["precision"] - xgb_metrics["precision"],
            "recall": rf_metrics["recall"] - xgb_metrics["recall"],
            "f1": rf_metrics["f1"] - xgb_metrics["f1"],
            "roc_auc": rf_metrics["roc_auc"] - xgb_metrics["roc_auc"],
            "pr_auc": rf_metrics["pr_auc"] - xgb_metrics["pr_auc"],
        },
        "prediction_complementarity": {
            "agreement_rate": overlap["prediction_agreement_rate"],
            "disagreement_count": overlap["prediction_disagreement_count"],
            "both_correct": overlap["both_correct"],
            "rf_only_correct": overlap["rf_only_correct"],
            "xgb_only_correct": overlap["xgb_only_correct"],
            "both_wrong": overlap["both_wrong"],
            "common_false_negatives": overlap["common_false_negatives"],
            "rf_only_false_negatives": overlap["rf_only_false_negatives"],
            "xgb_only_false_negatives": overlap["xgb_only_false_negatives"],
            "common_false_positives": overlap["common_false_positives"],
            "rf_only_false_positives": overlap["rf_only_false_positives"],
            "xgb_only_false_positives": overlap["xgb_only_false_positives"],
            "probability_correlation": overlap["probability_correlation"],
            "mean_absolute_probability_difference": overlap[
                "mean_absolute_probability_difference"
            ],
            "oracle_upper_bound_accuracy": overlap["oracle_upper_bound_accuracy"],
        },
        "feature_rank_summary": feature_summary,
        "interpretation_note": (
            "This is a descriptive validation-set comparison. The same validation set "
            "was used for hyperparameter selection, so final unbiased model performance "
            "must be measured only on the untouched test set after all model-development "
            "decisions, including any ensemble decision, are complete."
        ),
    }

    COMPARISON_DIR.mkdir(parents=True, exist_ok=True)

    metrics.to_csv(MODEL_METRICS_FILE, index=False)
    predictions.to_csv(PREDICTION_AGREEMENT_FILE, index=False)
    disagreements.to_csv(DISAGREEMENT_CASES_FILE, index=False)
    error_overlap.to_csv(ERROR_OVERLAP_FILE, index=False)
    group_errors.to_csv(GROUP_ERROR_FILE, index=False)
    feature_ranks.to_csv(FEATURE_RANK_FILE, index=False)

    with COMPARISON_REPORT_FILE.open("w", encoding="utf-8") as file:
        json.dump(_json_safe(report), file, indent=2)

    return {
        "predictions": predictions,
        "metrics": metrics,
        "error_overlap": error_overlap,
        "group_errors": group_errors,
        "feature_ranks": feature_ranks,
        "feature_summary": feature_summary,
        "disagreements": disagreements,
        "report": report,
    }