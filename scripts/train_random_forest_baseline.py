from src.model.random_forest_baseline import (
    CONFUSION_MATRIX_FILE,
    FEATURE_IMPORTANCE_FILE,
    METRICS_FILE,
    MODEL_FILE,
    MODEL_METADATA_FILE,
    VALIDATION_PREDICTIONS_FILE,
    train_random_forest_baseline
)

def main():
    print("Nairobi FloodWatch Random Forest Baseline Training")

    print("-" * 50)

    print("Loading frozen leakage-safe training and validation splits...")

    print("Training untuned Random Forest Baseline...")

    print("Test split: Untouched")

    results = (train_random_forest_baseline())

    metrics = (results["metrics"])
    feature_importance = (results["feature_importance"])
    confusion_matrix = (results["confusion_matrix"])

    print()

    print("Dataset Summary: ")

    print("-" * 50)

    print(f"Training rows: {len(results['train']):,}")
    print(f"Validation rows: {len(results['validation']):,}")

    print("Test rwos used: 0")

    print()

    print("Baseline Configurations: ")

    print("-" * 50)

    print("Number of trees: 300")
    print("Class weighting: None")
    print("Resampling: None")
    print("Decision threshold: 0.50")
    print("Random seed: 42")

    print()

    print("RF Baseline Perfomance: ")

    print("-" * 50)

    for split_name in ["train", "validation"]:
        row = (metrics[metrics["split"] == split_name].iloc[0])

        print(f"{split_name.capitalize()}:")
        print(f"Accuracy: {row['accuracy']:.4f}")
        print(f"Precision: {row['precision']:.4f}")
        print(f"Recall: {row['recall']:.4f}")
        print(f"F1 Score: {row['f1']:.4f}")
        print(f"ROC-AUC: {row['roc_auc']:.4f}")
        print(f"PR-AUC: {row['pr_auc']:.4f}")
        print()

    print("Validation Confusion Matrix: ")
    print(confusion_matrix.to_string())

    print()

    print("Performance Timing: ")
    print(f"Training time: {results['training_seconds']:.4f} seconds")
    print(f"Validation inference: {results['validation_inference_seconds']:.4f} seconds")
    print(f"Inference per row: {results['validation_inference_ms_per_row']:.4f} ms")

    print()

    print("Top 10 RF Feature Importances: ")

    print("-" * 50)

    top_features = feature_importance.head(10)
    for row in (top_features.itertuples(index=False)):
        print(f"{int(row.rank):02d}. {row.feature_name:<32} {row.importance:.4f}")

    print()

    print("Important Baseline Notes: ")

    print("Validation metrics are pre-tuning results")
    print("Threshold remains fixed at 0.50")
    print("The test split has not been accessed during RF baseline training")

    print()

    print(f"Saved model: {MODEL_FILE}")
    print(f"Saved model metadata: {MODEL_METADATA_FILE}")
    print(f"Saved metrics: {METRICS_FILE}")
    print(f"Saved confusion matrix: {CONFUSION_MATRIX_FILE}")
    print(f"Saved feature importance: {FEATURE_IMPORTANCE_FILE}")
    print(f"Saved validation predictions: {VALIDATION_PREDICTIONS_FILE}")

    print("Random Forest baseline training: OK")

if __name__ == "__main__":
    main()
