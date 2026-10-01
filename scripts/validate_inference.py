import json
import numpy as np
import pandas as pd
from config.paths import PROCESSED_DIR
from src.model.inference import FloodInferenceEngine

PROJECT_OUTPUT_DIR = PROCESSED_DIR.parent
SPLIT_DIR = PROJECT_OUTPUT_DIR / "features" / "modeling" / "splits"
FINAL_EVALUATION_DIR = PROCESSED_DIR / "modeling" / "final_evaluation"
INFERENCE_DIR = PROCESSED_DIR / "modeling" / "inference"
TEST_FILE = SPLIT_DIR / "test_model_features.csv"
EXPECTED_PREDICTIONS_FILE = FINAL_EVALUATION_DIR / "final_test_predictions.csv"
VALIDATION_REPORT_FILE = INFERENCE_DIR / "inference_replay_validation.json"

PROBABILITY_TOLERANCE = 1e-12

def main():
    print("Nairobi FloodWatch Production Inference Validation")

    print("=" * 50)

    test = pd.read_csv(TEST_FILE)
    expected = pd.read_csv(EXPECTED_PREDICTIONS_FILE)
    engine = FloodInferenceEngine()
    actual = engine.predict_batch(test)

    required_expected = {
        "model_record_id",
        "rf_probability",
        "rf_predicted_label",
    }

    missing = required_expected - set(expected.columns)

    if missing:
        raise ValueError(f"Final test prediction file is missing columns: {sorted(missing)}")

    actual = actual[["model_record_id", "flood_probability", "predicted_label"]].rename(columns={
        "flood_probability": "replayed_probability",
        "predicted_label": "replayed_label",
    })

    expected = expected[["model_record_id", "rf_probability", "rf_predicted_label"]]

    comparison = expected.merge(
        actual,
        on="model_record_id",
        how="outer",
        validate="one_to_one",
        indicator=True,
    )

    if not (comparison["_merge"] == "both").all():
        raise ValueError("Replay validation records do not exactly match Task 25 test records.")

    comparison = comparison.drop(columns="_merge")
    comparison["probability_difference"] = (comparison["rf_probability"] - comparison["replayed_probability"]).abs()

    maximum_probability_difference = float(comparison["probability_difference"].max())

    label_mismatches = int((comparison["rf_predicted_label"] != comparison["replayed_label"]).sum())

    probabilities_match = bool(
        np.allclose(
            comparison["rf_probability"],
            comparison["replayed_probability"],
            rtol=0.0,
            atol=PROBABILITY_TOLERANCE,
        )
    )

    labels_match = label_mismatches == 0

    if not probabilities_match:
        raise ValueError("Production inference probabilities do not reproduce Task 25 outputs.")

    if not labels_match:
        raise ValueError("Production inference labels do not reproduce Task 25 outputs.")

    INFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "validation_type": "production_inference_replay",
        "rows": len(comparison),
        "model_name": engine.model_name,
        "model_version": engine.model_version,
        "decision_threshold": engine.threshold,
        "probability_tolerance": PROBABILITY_TOLERANCE,
        "maximum_probability_difference": maximum_probability_difference,
        "probabilities_match": probabilities_match,
        "label_mismatches": label_mismatches,
        "labels_match": labels_match,
        "status": "PASS",
    }

    with VALIDATION_REPORT_FILE.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print(f"Replay rows: {len(comparison):,}")
    print(f"Model: {engine.model_name}")
    print(f"Model version: {engine.model_version}")
    print(f"Decision threshold: {engine.threshold:.2f}")
    print()

    print(f"Maximum probability difference: {maximum_probability_difference:.12f}")
    print(f"Probability match: {probabilities_match}")
    print(f"Label mismatches: {label_mismatches}")
    print(f"Label match: {labels_match}")
    print()

    print(f"Saved validation report: {VALIDATION_REPORT_FILE}")
    print()
    
    print("Production inference validation: PASS")


if __name__ == "__main__":
    main()