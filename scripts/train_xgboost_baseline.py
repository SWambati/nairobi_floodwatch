from src.model.xgboost_baseline import (
    CONFUSION_MATRIX_FILE,
    FEATURE_IMPORTANCE_FILE,
    METRICS_FILE,
    MODEL_FILE,
    MODEL_METADATA_FILE,
    VALIDATION_PREDICTIONS_FILE,
    train_xgboost_baseline,
)


def main():
    print("Nairobi FloodWatch XGBoost Baseline Training")

    print("=" * 50)

    print("Loading frozen leakage-safe training and validation splits...")

    print("Training untuned XGBoost comparison baseline...")

    print("Test split: untouched")

    results = (train_xgboost_baseline())
    metrics = (results["metrics"])
    feature_importance = (results["feature_importance"])
    confusion_matrix = (results["confusion_matrix"])

    print()
    print("Dataset Summary:")

    print("-" * 50)

    print(f"Training rows: {len(results['train']):,}")
    print(f"Validation rows: {len(results['validation']):,}")

    print("Test rows used: 0")

    print()

    print("Baseline Configuration:")

    print("-" * 50)

    print("Number of trees: 100")
    print("Learning rate: 0.30")
    print("Maximum depth: 6")
    print("Minimum child weight: 1")
    print("Subsample: 1.0")
    print("Column sample by tree: 1.0")
    print("L1 regularization: 0.0")
    print("L2 regularization: 1.0")
    print("Scale positive weight: 1.0")
    print("Resampling: None")
    print("Early stopping: None")
    print("Decision threshold: 0.50")
    print("Random seed: 42")

    print()

    print("XGBoost Baseline Performance:")

    print("-" * 50)

    for split_name in ["train", "validation",]:
        row = (metrics[metrics["split"] == split_name].iloc[0])

        print(f"{split_name.capitalize()}:")
        print(f"  Accuracy: {row['accuracy']:.4f}")
        print(f"  Precision: {row['precision']:.4f}")
        print(f"  Recall: {row['recall']:.4f}")
        print(f"  F1 Score: {row['f1']:.4f}")
        print(f"  ROC-AUC: {row['roc_auc']:.4f}")
        print(f"  PR-AUC: {row['pr_auc']:.4f}")

        print()

    print("Validation Confusion Matrix:")
    print(confusion_matrix.to_string())

    print()

    print("Performance Timing:")
    print(f"Training time: {results['training_seconds']:.4f} seconds")
    print(f"Validation inference: {results['validation_inference_seconds']:.6f} seconds")
    print(f"Inference per row: {results['validation_inference_ms_per_row']:.4f} ms")

    print()

    print("Top 10 XGBoost Feature Importances: ")

    print("-" * 50)

    top_features = (feature_importance.head(10))

    for row in (top_features.itertuples(index=False)):
        print(f" {int(row.rank):02d}. {row.feature_name:<32} {row.importance:.4f}")

    print()

    train_row = (metrics[metrics["split"] == "train"].iloc[0])
    validation_row = (metrics[metrics["split"] == "validation"].iloc[0])

    f1_gap = (train_row["f1"] - validation_row["f1"])

    print("Generalization Diagnostic:")
    print(f"  Train-validation F1 gap: {f1_gap:.4f}")

    print()

    print("Important Baseline Notes:")
    print("Validation metrics are pre-tuning results.")
    print("Threshold remains fixed at 0.50.")
    print("No early stopping has been applied.")

    print("The test split has not been accessed.")

    print()

    print(f"Saved model: {MODEL_FILE}")
    print(f"Saved model metadata: {MODEL_METADATA_FILE}")
    print(f"Saved metrics: {METRICS_FILE}")
    print(f"Saved confusion matrix: {CONFUSION_MATRIX_FILE}")
    print(f"Saved feature importance: {FEATURE_IMPORTANCE_FILE}")
    print(f"Saved validation predictions: {VALIDATION_PREDICTIONS_FILE}")

    print()

    print("XGBoost baseline training: OK")


if __name__ == "__main__":
    main()