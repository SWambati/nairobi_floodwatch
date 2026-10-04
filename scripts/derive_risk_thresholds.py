from src.model.risk_classification import (RISK_THRESHOLDS_FILE, RISK_THRESHOLD_SEARCH_FILE, derive_risk_thresholds)

def main():
    print("Nairobi FloodWatch Risk Threshold Derivation")

    print("=" * 50)

    print("Source: Tuned Random Forest Classifier Validation Predictions")

    print("Moderate-risk selection: maximum F2")
    print("High-risk selection: maximum F0.5")

    print("Test set used: No")

    print()

    thresholds = derive_risk_thresholds()
    moderate = thresholds["moderate_risk"]
    high = thresholds["high_risk"]

    print("Derived risk thresholds: ")
    print(f"Low: score < {moderate['threshold']:.4f}")
    print(f"Moderate: {moderate['threshold']:.4f} <= score < {high['threshold']:.4f} ")
    print(f"High: score >= {high['threshold']:.4f}")
    print()

    print("Moderate Threshold validation characteristics: ")
    print(f"Precision: {moderate['validation_precision']:.4f}")
    print(f"Recall: {moderate['validation_recall']:.4f}")
    print(f"F2 Score: {moderate['validation_f2']:.4f}")
    print()

    print("High Threshold validation characteristics: ")
    print(f"Precision: {high['validation_precision']:.4f}")
    print(f"Recall: {high['validation_recall']:.4f}")
    print(f"F0.5 Score: {high['validation_f0_5']:.4f}")
    print()

    print(f"Saved thresholds: {RISK_THRESHOLDS_FILE}")
    print(f"Saved threshold search results: {RISK_THRESHOLD_SEARCH_FILE}")
    print()

    print("Risk threshold derivation: OK")


if __name__ == "__main__":
    main()
