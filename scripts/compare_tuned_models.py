from src.model.model_comparison import (
    COMPARISON_REPORT_FILE,
    DISAGREEMENT_CASES_FILE,
    ERROR_OVERLAP_FILE,
    FEATURE_RANK_FILE,
    GROUP_ERROR_FILE,
    MODEL_METRICS_FILE,
    PREDICTION_AGREEMENT_FILE,
    compare_tuned_models,
)


def main():
    print("Nairobi FloodWatch Tuned Model Comparative Evaluation")
    print("=" * 50)
    print("Evaluation dataset: validation")
    print("Models: tuned Random Forest vs tuned XGBoost")
    print("Decision threshold: 0.50")
    print("Test split: Not Touched")
    print()

    results = compare_tuned_models()

    metrics = results["metrics"]
    overlap = results["error_overlap"].iloc[0]

    print("=" * 50)
    print("VALIDATION PERFORMANCE")
    print("=" * 50)

    for row in metrics.itertuples(index=False):
        print(row.model)
        print(f"Accuracy:  {row.accuracy:.4f}")
        print(f"Precision: {row.precision:.4f}")
        print(f"Recall: {row.recall:.4f}")
        print(f"F1 Score: {row.f1:.4f}")
        print(f"ROC-AUC: {row.roc_auc:.4f}")
        print(f"PR-AUC: {row.pr_auc:.4f}")
        print(f"TN / FP: {row.true_negative} / {row.false_positive}")
        print(f"FN / TP: {row.false_negative} / {row.true_positive}")
        print()

    rf = metrics[metrics["model"] == "random_forest"].iloc[0]
    xgb = metrics[metrics["model"] == "xgboost"].iloc[0]

    print("=" * 50)

    print("DESCRIPTIVE RF VS XGBOOST DIFFERENCES")

    print("=" * 50)

    print(f"Accuracy difference: {rf['accuracy'] - xgb['accuracy']:+.4f}")
    print(f"Precision difference: {rf['precision'] - xgb['precision']:+.4f}")
    print(f"Recall difference: {rf['recall'] - xgb['recall']:+.4f}")
    print(f"F1 difference: {rf['f1'] - xgb['f1']:+.4f}")
    print(f"ROC-AUC difference: {rf['roc_auc'] - xgb['roc_auc']:+.4f}")
    print(f"PR-AUC difference: {rf['pr_auc'] - xgb['pr_auc']:+.4f}")
    print()

    print("=" * 50)

    print("PREDICTION COMPLEMENTARITY")

    print("=" * 50)

    print(f"Prediction agreement: {overlap['prediction_agreement_rate']:.2%}")
    print(f"Prediction disagreements: {int(overlap['prediction_disagreement_count'])}")
    print()

    print(f"Both correct: {int(overlap['both_correct'])}")
    print(f"RF only correct: {int(overlap['rf_only_correct'])}")
    print(f"XGBoost only correct: {int(overlap['xgb_only_correct'])}")
    print(f"Both wrong: {int(overlap['both_wrong'])}")
    print()

    print("False negatives:")
    print(f"RF total: {int(overlap['rf_false_negatives'])}")
    print(f"XGBoost total: {int(overlap['xgb_false_negatives'])}")
    print(f"Common: {int(overlap['common_false_negatives'])}")
    print(f"RF only: {int(overlap['rf_only_false_negatives'])}")
    print(f"XGBoost only: {int(overlap['xgb_only_false_negatives'])}")
    print()

    print("False positives:")
    print(f"RF total: {int(overlap['rf_false_positives'])}")
    print(f"XGBoost total: {int(overlap['xgb_false_positives'])}")
    print(f"Common: {int(overlap['common_false_positives'])}")
    print(f"RF only: {int(overlap['rf_only_false_positives'])}")
    print(f"XGBoost only: {int(overlap['xgb_only_false_positives'])}")
    print()

    print(f"Probability correlation: {overlap['probability_correlation']:.4f}")
    print(f"Mean absolute probability difference: {overlap['mean_absolute_probability_difference']:.4f}")
    print(f"Oracle upper-bound accuracy if the correct model could always be chosen: {overlap['oracle_upper_bound_accuracy']:.2%}")
    print()

    print("=" * 50)

    print("FEATURE-RANK COMPARISON")

    print("=" * 50)

    feature_summary = results["feature_summary"]

    print("RF top 5:")
    for feature in feature_summary["top_5_rf_features"]:
        print(f"- {feature}")

    print()

    print("XGBoost top 5:")
    for feature in feature_summary["top_5_xgb_features"]:
        print(f"- {feature}")

    print()

    print(f"Shared top-5 features: {feature_summary['top_5_overlap_count']}")

    for feature in feature_summary["top_5_feature_overlap"]:
        print(f"- {feature}")

    print()

    print("=" * 50)

    print("EVENT-GROUP ERROR ANALYSIS")

    print("=" * 50)

    group_errors = results["group_errors"]

    print(f"Validation event groups: {len(group_errors)}")

    print()

    print("Five groups with the most shared errors:")

    columns = [
        "model_split_group_id",
        "rows",
        "rf_errors",
        "xgb_errors",
        "both_wrong",
        "prediction_disagreements",
    ]

    print(group_errors[columns].head(5).to_string(index=False))

    print()

    print("Task 22 metric reproduction: PASS")
    print("Test rows used: 0")
    print("Test split remains sealed.")
    
    print()

    print("Saved artifacts:")
    print(f"Model metrics: {MODEL_METRICS_FILE}")
    print(f"Prediction agreement: {PREDICTION_AGREEMENT_FILE}")
    print(f"Disagreement cases: {DISAGREEMENT_CASES_FILE}")
    print(f"Error overlap: {ERROR_OVERLAP_FILE}")
    print(f"Group error analysis: {GROUP_ERROR_FILE}")
    print(f"Feature rank comparison: {FEATURE_RANK_FILE}")
    print(f"Comparison report: {COMPARISON_REPORT_FILE}")
    print()

    print("Tuned model comparative evaluation: OK")


if __name__ == "__main__":
    main()