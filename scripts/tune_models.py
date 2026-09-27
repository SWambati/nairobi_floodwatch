from src.model.hyperparameter_tuning import (
    BEST_PARAMS_FILE,
    GROUP_DIAGNOSTIC_FILE,
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


def print_model_results(model_name, search, evaluation):
    train = (evaluation["train_metrics"])
    validation = (evaluation["validation_metrics"])

    print("=" * 50)

    print(f"BEST {model_name.upper()} PARAMETERS")

    print("=" * 50)

    for key, value in (evaluation["parameters"].items()):
        print(f"{key}: {value}")

    print()

    print(f"Selected candidate: {search['best_candidate_id']}")
    print(f"Search time: {search['search_seconds']:.2f} seconds")

    print()

    print("Training: ")
    print(f"Accuracy: {train['accuracy']:.4f}")
    print(f"Precision: {train['precision']:.4f}")
    print(f"Recall: {train['recall']:.4f}")
    print(f"F1: {train['f1']:.4f}")
    print(f"ROC-AUC: {train['roc_auc']:.4f}")
    print(f"PR-AUC: {train['pr_auc']:.4f}")

    print()

    print("Validation: ")
    print(f"Accuracy: {validation['accuracy']:.4f}")
    print(f"Precision: {validation['precision']:.4f}")
    print(f"Recall: {validation['recall']:.4f}")
    print(f"F1: {validation['f1']:.4f}")
    print(f"ROC-AUC: {validation['roc_auc']:.4f}")
    print(f"PR-AUC: {validation['pr_auc']:.4f}")

    print()

    print(f"Train-validation F1 gap: {evaluation['generalization_gap']:.4f}")

    print()

    print("Validation confusion matrix:")
    print(f"TN: {validation['true_negative']}")
    print(f"FP: {validation['false_positive']}")
    print(f"FN: {validation['false_negative']}")
    print(f"TP: {validation['true_positive']}")

    print()


def main():
    print("Nairobi FloodWatch Model Hyperparameter Tuning")

    print("=" * 50)

    print("Search method: validation-guided randomized search")
    print("Candidate models are fitted using training data only.")
    print("Hyperparameters are selected using the dedicated validation set.")
    print("Primary selection metric: F1")
    print("Tie-break 1: ROC-AUC")
    print("Tie-break 2: PR-AUC")
    print("Decision threshold: 0.50")
    print("Test split: untouched")

    print()

    print("Cross-validation is not used because the training event groups are extremely uneven in size.")

    print()

    print("Random Forest search:")
    print(f"Randomized candidates: {RF_SEARCH_ITERATIONS}")

    print()

    print("XGBoost search:")
    print(f"Randomized candidates: {XGB_SEARCH_ITERATIONS}")

    print()

    results = (tune_models())
    group_diagnostic = (results["group_diagnostic"])

    print()
    print("=" * 50)

    print("TRAINING EVENT-GROUP DIAGNOSTIC")

    print("=" * 50)

    print(f"Training rows: {len(results['train']):,}")
    print(f"Training groups: {len(group_diagnostic):,}")
    print(f"Median group size: {group_diagnostic['row_count'].median():.0f}")
    print(f"75th percentile group size: {group_diagnostic['row_count'].quantile(0.75):.0f}")
    print(f"Largest group size: {group_diagnostic['row_count'].max():,}")

    largest_ratio = (
        group_diagnostic[
            "row_count"
        ].max()
        / len(
            results[
                "train"
            ]
        )
    )

    print(f"Largest group share of training set: {largest_ratio:.2%}")

    print()

    print(f"All groups internally class-balanced: {group_diagnostic['balanced_group'].all()}")

    print()

    print_model_results("Random Forest", results["rf_search"], results["rf_evaluation"],)
    print_model_results("XGBoost", results["xgb_search"], results["xgb_evaluation"],)

    print("=" * 50)

    print("BASELINE VS TUNED VALIDATION CHANGES")

    print("=" * 50)

    comparison = (results["comparison"])

    for row in (comparison.itertuples(index=False)):
        print(row.model)

        if hasattr(row, "baseline_validation_f1"):
            print(f"Baseline F1: {row.baseline_validation_f1:.4f}")
            print(f"Tuned F1: {row.validation_f1:.4f}")
            print(f"F1 change: {row.f1_change_from_baseline:+.4f}")
            print(f"ROC-AUC change: {row.roc_auc_change_from_baseline:+.4f}")
            print(f"PR-AUC change: {row.pr_auc_change_from_baseline:+.4f}")

        print(f"Train-validation F1 gap: {row.f1_generalization_gap:.4f}")

        print()

    print("Test rows used: 0")
    print("Test split remains sealed.")

    print()

    print("Saved artifacts: ")
    print(f"Tuned RF: {RF_MODEL_FILE}")
    print(f"Tuned XGBoost: {XGB_MODEL_FILE}")
    print(f"RF search results: {RF_SEARCH_RESULTS_FILE}")
    print(f"XGBoost search results: {XGB_SEARCH_RESULTS_FILE}")
    print(f"Training group diagnostic: {GROUP_DIAGNOSTIC_FILE}")
    print(f"Best parameters: {BEST_PARAMS_FILE}")
    print(f"Tuned metrics: {TUNED_METRICS_FILE}")
    print(f"Tuned comparison: {TUNED_COMPARISON_FILE}")

    print()
    print("Model hyperparameter tuning: OK")

if __name__ == "__main__":
    main()