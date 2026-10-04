import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import (fbeta_score, precision_score, recall_score)
from config.paths import PROCESSED_DIR


# Configuration
MODERATE_BETA = 2.0
HIGH_BETA = 0.5


# Paths
TUNING_DIR = PROCESSED_DIR / "modeling" / "tuning"
RISK_DIR = PROCESSED_DIR / "modeling" / "risk_classification"
VALIDATION_PREDICTIONS_FILE = (TUNING_DIR / "random_forest_tuned_validation_predictions.csv")
RISK_THRESHOLDS_FILE = RISK_DIR / "risk_thresholds.json"
RISK_THRESHOLD_SEARCH_FILE = RISK_DIR / "risk_threshold_search.csv"


# Helpers
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


def _validate_probability_series(probabilities: pd.Series) -> None:
    if probabilities.isna().any():
        raise ValueError("Flood-risk scores contain missing values.")

    if not np.isfinite(probabilities.to_numpy(dtype=float)).all():
        raise ValueError("Flood-risk scores contain non-finite values.")

    if not probabilities.between(0.0, 1.0).all():
        raise ValueError("Flood-risk scores must be between 0 and 1.")


# Validation predictions
def load_validation_predictions() -> pd.DataFrame:
    if not VALIDATION_PREDICTIONS_FILE.exists():
        raise FileNotFoundError(f"RF validation predictions not found: {VALIDATION_PREDICTIONS_FILE}")

    predictions = pd.read_csv(VALIDATION_PREDICTIONS_FILE)

    required_columns = {
        "model_record_id",
        "flood_label",
        "flood_probability",
    }

    missing = required_columns - set(predictions.columns)

    if missing:
        raise ValueError(f"Validation prediction file is missing columns: {sorted(missing)}")

    if predictions["model_record_id"].duplicated().any():
        raise ValueError("Validation predictions contain duplicate model IDs.")

    predictions["flood_label"] = pd.to_numeric(predictions["flood_label"], errors="raise").astype(int)
    predictions["flood_probability"] = pd.to_numeric(predictions["flood_probability"], errors="raise")

    if set(predictions["flood_label"].unique()) != {0, 1}:
        raise ValueError("Validation predictions must contain both binary classes.")

    _validate_probability_series(predictions["flood_probability"])

    return predictions


# Threshold search
def build_threshold_search(predictions: pd.DataFrame) -> pd.DataFrame:
    y_true = predictions["flood_label"].to_numpy(dtype=int)
    probabilities = predictions["flood_probability"].to_numpy(dtype=float)

    thresholds = np.unique(probabilities)

    rows = []

    for threshold in thresholds:
        y_pred = (probabilities >= threshold).astype(int)

        precision = precision_score(y_true, y_pred, zero_division=0)
        recall = recall_score(y_true, y_pred, zero_division=0)
        f2 = fbeta_score(y_true, y_pred, beta=MODERATE_BETA, zero_division=0)
        f05 = fbeta_score(y_true, y_pred, beta=HIGH_BETA, zero_division=0)

        rows.append({
            "threshold": float(threshold),
            "precision": float(precision),
            "recall": float(recall),
            "f2": float(f2),
            "f0_5": float(f05),
            "predicted_positive": int(y_pred.sum()),
        })

    return pd.DataFrame(rows)


def select_moderate_threshold(search: pd.DataFrame) -> pd.Series:
    ranked = search.sort_values(by=["f2", "recall", "threshold"], ascending=[False, False, False])

    return ranked.iloc[0]


def select_high_threshold(search: pd.DataFrame) -> pd.Series:
    ranked = search.sort_values(by=["f0_5", "precision", "threshold"], ascending=[False, False, False])

    return ranked.iloc[0]


# Threshold derivation
def derive_risk_thresholds() -> dict:
    predictions = load_validation_predictions()
    search = build_threshold_search(predictions)

    moderate = select_moderate_threshold(search)
    high = select_high_threshold(search)

    moderate_threshold = float(moderate["threshold"])
    high_threshold = float(high["threshold"])

    if moderate_threshold >= high_threshold:
        raise ValueError("Derived Moderate threshold is not below the High threshold. Risk bands cannot be constructed safely.")

    configuration = {
        "classification_type": "model_score_risk_bands",
        "source": "Tuned Random Forest validation predictions",
        "validation_rows": len(predictions),
        "low_risk": {"rule": "score < moderate_threshold"},
        "moderate_risk": {
            "rule": "moderate_threshold <= score < high_threshold",
            "threshold": moderate_threshold,
            "selection_method": "maximum F2 score",
            "beta": MODERATE_BETA,
            "validation_precision": float(moderate["precision"]),
            "validation_recall": float(moderate["recall"]),
            "validation_f2": float(moderate["f2"]),
        },
        "high_risk": {
            "rule": "score >= high_threshold",
            "threshold": high_threshold,
            "selection_method": "maximum F0.5 score",
            "beta": HIGH_BETA,
            "validation_precision": float(high["precision"]),
            "validation_recall": float(high["recall"]),
            "validation_f0_5": float(high["f0_5"]),
        },
        "interpretation_note": ("Risk bands are derived from Random Forest model scores and should not be interpreted as calibrated real-world flood probabilities."),
        "test_set_used_for_threshold_selection": False,
    }

    RISK_DIR.mkdir(parents=True, exist_ok=True)

    search.to_csv(RISK_THRESHOLD_SEARCH_FILE, index=False)

    with RISK_THRESHOLDS_FILE.open("w", encoding="utf-8") as file:
        json.dump(_json_safe(configuration), file, indent=2)

    return configuration


# Production risk classifier
class FloodRiskClassifier:
    def __init__(self, threshold_file: Path = RISK_THRESHOLDS_FILE):
        self.threshold_file = Path(threshold_file)

        if not self.threshold_file.exists():
            raise FileNotFoundError(f"Risk threshold configuration not found: {self.threshold_file}")

        with self.threshold_file.open("r", encoding="utf-8") as file:
            self.configuration = json.load(file)

        self.moderate_threshold = float(self.configuration["moderate_risk"]["threshold"])
        self.high_threshold = float(self.configuration["high_risk"]["threshold"])

        if not 0.0 <= self.moderate_threshold < self.high_threshold <= 1.0:
            raise ValueError("Stored risk thresholds are invalid.")

    def classify_score(self, score: float) -> str:
        score = float(score)

        if not np.isfinite(score):
            raise ValueError("Flood-risk score must be finite.")

        if not 0.0 <= score <= 1.0:
            raise ValueError("Flood-risk score must be between 0 and 1.")

        if score >= self.high_threshold:
            return "HIGH"

        if score >= self.moderate_threshold:
            return "MODERATE"

        return "LOW"

    def classify_batch(self, dataframe: pd.DataFrame, probability_column: str = "flood_probability") -> pd.DataFrame:
        if probability_column not in dataframe.columns:
            raise ValueError(f"Input data is missing probability column: {probability_column}")

        output = dataframe.copy()
        output[probability_column] = pd.to_numeric(output[probability_column], errors="coerce")

        _validate_probability_series(output[probability_column])

        output["risk_level"] = output[probability_column].apply(self.classify_score)
        output["moderate_threshold"] = self.moderate_threshold
        output["high_threshold"] = self.high_threshold

        return output

    def threshold_info(self) -> dict:
        return {
            "moderate_threshold": self.moderate_threshold,
            "high_threshold": self.high_threshold,
            "classification_type": self.configuration["classification_type"],
        }