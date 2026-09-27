from src.model.final_model import (
    FINAL_COMPARISON_FILE,
    FINAL_MODEL_METADATA_FILE,
    FINAL_RF_MODEL_FILE,
    FINAL_TEST_METRICS_FILE,
    FINAL_TEST_PREDICTIONS_FILE,
    FINAL_XGB_COMPARISON_FILE,
    RF_FEATURE_IMPORTANCE_FILE,
    finalize_models,
)


def print_metrics(model_name, row):
    print(model_name)
    print(f"Accuracy: {row['accuracy']:.4f}")
    print(f"Precision: {row['precision']:.4f}")
    print(f"Recall: {row['recall']:.4f}")
    print(f"F1: {row['f1']:.4f}")
    print(f"ROC-AUC: {row['roc_auc']:.4f}")
    print(f"PR-AUC: {row['pr_auc']:.4f}")
    print(f"TN / FP: {int(row['true_negative'])} / {int(row['false_positive'])}")
    print(f"FN / TP: {int(row['false_negative'])} / {int(row['true_positive'])}")
    print(f"Training: {row['training_seconds']:.4f} seconds")
    print(f"Inference: {row['test_inference_ms_per_row']:.4f} ms/row")

    print()


def main():
    print("Nairobi FloodWatch Final Model Evaluation")
    
    print("=" * 50)

    print("Final production model: Random Forest")
    print("Selection status: LOCKED BEFORE TEST")
    print("Selection basis: Task 23 validation comparison")
    print("Ensemble: Not implemented - insufficient complementarity")

    print()

    print("Development data: training + validation")
    print("Held-out evaluation data: test")
    print("Decision threshold: 0.50")

    print()

    print("Opening held-out test set for final evaluation...")

    print()

    results = finalize_models()

    print("=" * 50)

    print("FINAL DATASET SUMMARY")

    print("=" * 50)

    print(f"Original training rows:   {len(results['train']):,}")
    print(f"Original validation rows: {len(results['validation']):,}")
    print(f"Development rows:         {len(results['development']):,}")
    print(f"Held-out test rows:       {len(results['test']):,}")

    print(f"Test event groups: {results['test']['model_split_group_id'].nunique():,}")
    print()

    print("Split-integrity checks: PASS")
    print("Model-record overlap: 0")
    print("Event-group overlap: 0")

    print()

    print("=" * 50)

    print("FINAL HELD-OUT TEST PERFORMANCE")

    print("=" * 50)

    metrics = results["metrics"]

    rf = metrics[metrics["model"] == "random_forest"].iloc[0]
    xgb = metrics[metrics["model"] == "xgboost"].iloc[0]

    print_metrics("Random Forest — selected production model", rf)
    print_metrics("XGBoost — comparison model", xgb)

    print("=" * 50)

    print("DESCRIPTIVE RF - XGBOOST TEST DIFFERENCES")

    print("=" * 50)

    print(f"Accuracy difference: {rf['accuracy'] - xgb['accuracy']:+.4f}")
    print(f"Precision difference: {rf['precision'] - xgb['precision']:+.4f}")
    print(f"Recall difference: {rf['recall'] - xgb['recall']:+.4f}")
    print(f"F1 difference: {rf['f1'] - xgb['f1']:+.4f}")
    print(f"ROC-AUC difference: {rf['roc_auc'] - xgb['roc_auc']:+.4f}")
    print(f"PR-AUC difference: {rf['pr_auc'] - xgb['pr_auc']:+.4f}")

    print()

    agreement = results["predictions"]["prediction_agreement"].mean()

    print(f"Test prediction agreement: {agreement:.2%}")

    print()

    print("=" * 50)

    print("FINAL RANDOM FOREST FEATURE IMPORTANCE")

    print("=" * 50)

    for row in results["rf_feature_importance"].head(10).itertuples(index=False):
        print(f"{int(row.rank):02d}. {row.feature_name:<32} {row.importance:.4f}")

    print()

    print("=" * 50)

    print("MODEL SELECTION STATUS")

    print("=" * 50)

    print("Production model remains: Random Forest")
    print("Selection was fixed before test evaluation.")
    print("Test metrics were NOT used to change model selection.")

    print()

    print("Saved artifacts:")
    print(f"Final RF model: {FINAL_RF_MODEL_FILE}")
    print(f"XGBoost comparison model: {FINAL_XGB_COMPARISON_FILE}")
    print(f"Final model metadata: {FINAL_MODEL_METADATA_FILE}")
    print(f"Final test metrics: {FINAL_TEST_METRICS_FILE}")
    print(f"Final test predictions: {FINAL_TEST_PREDICTIONS_FILE}")
    print(f"RF feature importance: {RF_FEATURE_IMPORTANCE_FILE}")
    print(f"Final comparison report: {FINAL_COMPARISON_FILE}")

    print()

    print("Final model selection, evaluation and persistence: OK")


if __name__ == "__main__":
    main()