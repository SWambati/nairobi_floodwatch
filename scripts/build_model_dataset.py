from src.model.model_dataset import (
    ENVIRONMENTAL_PREDICTORS,
    FEATURE_MANIFEST_FILE,
    FEATURE_MATRIX_FILE,
    FULL_DATASET_FILE,
    PREDICTOR_COLUMNS,
    VALIDATION_REPORT_FILE,
    WEATHER_PREDICTORS,
    build_final_model_dataset,
)


def main():

    print("Nairobi FloodWatch Historical Model Dataset Assembly")

    print("=" * 50)

    print("Loading model labels...")

    print("Joining historical weather features...")

    print("Joining historical environmental features...")

    print("Validating predictor schema and leakage controls...")

    (
        dataset,
        feature_matrix,
        manifest,
        report,
    ) = build_final_model_dataset()

    print()
    print("Final modelling dataset:")

    print("-" * 50)

    print(f"Rows: {len(dataset):,}")
    print(f"Model predictors: {len(PREDICTOR_COLUMNS)}")
    print(f"Weather predictors: {len(WEATHER_PREDICTORS)}")
    print(f"Environmental predictors: {len(ENVIRONMENTAL_PREDICTORS)}")

    print()

    print("Class distribution:")

    class_counts = (dataset["flood_label"].value_counts().sort_index(ascending=False))

    for label, count in (class_counts.items()):
        label_name = ("Flood" if label == 1 else "Presumed non-flood")
        print(f"  {label} ({label_name}): {count:,}")

    print()
    print("Predictor schema:")

    for index, feature in enumerate(PREDICTOR_COLUMNS, start=1,):
        print(f"  {index:02d}. {feature}")

    print()

    print("Leakage controls:")

    print("latitude / longitude: excluded from predictors")
    print("administrative names: excluded from predictors")
    print("WorldCover 2021: excluded from predictors")
    print("GHSL epoch: provenance only")
    print("GHSL built surface m2: excluded as redundant")
    print("observation/event IDs: metadata only")

    print()

    print("Validation:")

    for row in report.itertuples(index=False):
        print(f"  {row.metric}: {row.value}")

    print()

    print(f"Saved complete model dataset: {FULL_DATASET_FILE}")
    print(f"Saved RF/XGBoost feature matrix: {FEATURE_MATRIX_FILE}")
    print(f"Saved feature manifest: {FEATURE_MANIFEST_FILE}")
    print(f"Saved validation report: {VALIDATION_REPORT_FILE}")

    print()

    print("Historical model dataset assembly: OK")


if __name__ == "__main__":
    main()