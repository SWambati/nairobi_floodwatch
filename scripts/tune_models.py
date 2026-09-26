from src.model.hyperparameter_tuning import (
    BEST_PARAMS_FILE,
    CV_FOLD_REPORT_FILE,
    RF_MODEL_FILE,
    RF_SEARCH_ITERATIONS,
    RF_SEARCH_RESULTS_FILE,
    TUNED_COMPARISON_FILE,
    TUNED_METRICS_FILE,
    XGB_MODEL_FILE,
    XGB_SEARCH_ITERATIONS,
    XGB_SEARCH_RESULTS_FILE,
    tune_models,
)


def print_metrics(model_name, evaluation):
    train = (evaluation["train_metrics"])
    validation = (evaluation["validation_metrics"])

    print(f"{model_name}:")

    print("Train:")

    print(f"Accuracy: {train['accuracy']:.4f}")
    print(f"Precision: {train['precision']:.4f}")
    print(f"Recall: {train['recall']:.4f}")
    print(f"F1: {train['f1']:.4f}")
    print(f"ROC-AUC: {train['roc_auc']:.4f}")

    print()

    print("Validation:")
    print(f"Accuracy: {validation['accuracy']:.4f}")
    print(f"Precision: {validation['precision']:.4f}")
    print(f"F1: {validation['f1']:.4f}")
    print(f"Recall: {validation['recall']:.4f}")
    print(f"ROC-AUC: {validation['roc_auc']:.4f}")
    print(f"PR-AUC: {validation['pr_auc']:.4f}")
    print(f"F1 generalization gap: {train['f1'] - validation['f1']:.4f}")

    print()


def main():
    print("Nairobi FloodWatch Model Hyperparameter Tuning")

    print("=" * 50)

    print("Training dataset only will be used for hyperparameter search.")
    print("Cross-validation: 5-fold StratifiedGroupKFold")
    print("Optimization metric: F1")
    print("Validation threshold: 0.50")
    print("Test split: untouched")

    print()

    print("Random Forest search:")
    print(f"  Randomized candidates: {RF_SEARCH_ITERATIONS}")

    print("Cross Validation folds: 5")

    print(f"Total fits: {RF_SEARCH_ITERATIONS * 5}")

    print()

    print("XGBoost search:")
    print(f"  Randomized candidates: {XGB_SEARCH_ITERATIONS}")
    print("  CV folds: 5")
    print(f"  Total fits: {XGB_SEARCH_ITERATIONS * 5}")

    print()

    results = (tune_models())

    print()
    print("=" * 50)
    print("GROUP-AWARE Cross Validation VALIDATION")

    print("=" * 50)

    print(results["cv_fold_report"].to_string( index=False))

    print()

    print(f"All CV group overlaps: {int(results['cv_fold_report']['group_overlap'].sum())}")

    print()

    print("=" * 50)

    print("BEST RANDOM FOREST PARAMETERS")

    print("=" * 50)

    for key, value in (results["rf_search"].best_params_.items()):
        print(f"{key}: {value}")

    print()

    rf_cv = (results["rf_cv_summary"])

    print(f"Best CV F1: {rf_cv['best_cv_f1']:.4f} ± {rf_cv['best_cv_f1_std']:.4f}")
    print(f"Mean CV train F1: {rf_cv['best_cv_train_f1']:.4f}")
    print(f"CV ROC-AUC: {rf_cv['best_cv_roc_auc']:.4f}")
    print(f"CV Average Precision: {rf_cv['best_cv_average_precision']:.4f}")
    print(f"Search time: {results['rf_search_seconds']:.2f} seconds")

    print()

    print("=" * 50)

    print("BEST XGBOOST PARAMETERS")

    print("=" * 50)

    for key, value in results["xgb_search"].best_params_.items():
        print(f"{key}: {value}")

    print()

    xgb_cv = results["xgb_cv_summary"]
    print(f"Best CV F1: {xgb_cv['best_cv_f1']:.4f} ± {xgb_cv['best_cv_f1_std']:.4f}")
    print(f"Mean CV train F1: {xgb_cv['best_cv_train_f1']:.4f}")
    print(f"CV ROC-AUC: {xgb_cv['best_cv_roc_auc']:.4f}")
    print(f"CV Average Precision: {xgb_cv['best_cv_average_precision']:.4f}")
    print(f"Search time: {results['xgb_search_seconds']:.2f} seconds")

    print()

    print("=" * 50)

    print("TUNED MODEL PERFORMANCE")

    print("=" * 50)

    print_metrics("Random Forest", results["rf_evaluation"],)
    print_metrics("XGBoost", results["xgb_evaluation"],)

    print("=" * 50)

    print("BASELINE → TUNED VALIDATION CHANGES")

    print("=" * 50)

    comparison = (results["comparison"])

    for row in (comparison.itertuples(index=False)):
        print(row.model)

        if hasattr(row, "baseline_validation_f1"):
            print(f"  Baseline F1: {row.baseline_validation_f1:.4f}")
            print(f"  Tuned F1: {row.validation_f1:.4f}")
            print(f"  F1 change: {row.f1_change_from_baseline:+.4f}")
            print(f"  ROC-AUC change: {row.roc_auc_change_from_baseline:+.4f}")
            print(f"  PR-AUC change: {row.pr_auc_change_from_baseline:+.4f}")

        print()

    print("Test rows used: 0")
   
    print()

    print("Saved artifacts:")

    print(f"  Tuned RF: {RF_MODEL_FILE}")
    print(f"  Tuned XGBoost: {XGB_MODEL_FILE}")
    print(f"  RF search results: {RF_SEARCH_RESULTS_FILE}")
    print(f"  XGBoost search results: {XGB_SEARCH_RESULTS_FILE}")
    print(f"  CV fold report: {CV_FOLD_REPORT_FILE}")
    print(f"  Best parameters: {BEST_PARAMS_FILE}")
    print(f"  Tuned metrics: {TUNED_METRICS_FILE}")
    print(f"  Tuned comparison: {TUNED_COMPARISON_FILE}")

    print()

    print("Model hyperparameter tuning: OK")

if __name__ == "__main__":
    main()