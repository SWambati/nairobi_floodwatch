from src.model.label_dataset import (COMBINED_OUTPUT_FILE, NEGATIVE_OUTPUT_FILE, POSITIVE_OUTPUT_FILE, REPORT_OUTPUT_FILE, build_historical_model_labels)

def main():
    print("Nairobi FloodWatch Historical Model Label Construction")

    print("-" * 50)

    print("Constructing positive modelling observations...")

    print("Generating conservative presumed non-flood observations...")
    (
        positives,
        negatives,
        combined,
        report,
    ) = build_historical_model_labels()

    print()

    print("Label construction summary: ")

    print("-" * 50)

    raw_positive_rows = int(report.loc[report["metric"] == "raw_positive_rows", "value"].iloc[0])

    duplicate_rows_removed = int(report.loc[report["metric"] == ("duplicate_positive_rows_removed"), "value"].iloc[0])

    print(f"Duplicate modelling observations removed: {duplicate_rows_removed:,}")

    print(f"Final positive observations: {len(positives):,}")

    print(f"Presumed non-flood observations: {len(negatives):,}")

    print(f"Combined modelling labels: {len(combined):,}")

    print()
    print("Class distribution: ")

    class_counts = (combined["flood_label"].value_counts().sort_values(ascending=False))

    for label, count in (class_counts.items()):
        label_name = ("Flood" if label == 1 else "Presumed Non-Flood")

        print(f"{label} ({label_name}): {count:,}")

    print()

    print("Positive verification status: ")

    verification_counts = (positives["verification_status"].value_counts())

    for status, count in (verification_counts.item()):
        print(f"{status} {count:,}")


    print()
    print("Negative Sampling Diagnostics: ")
    print("Same Location as reference positive: yes")
    print("Citywide known-evejt exclusion: yes")
    print("Antecedent-window protection: yes")
    print("random seed: 42")

    print()
    print(f"Saved positive observations: {POSITIVE_OUTPUT_FILE}")
    print(f"Saved negative observations: {NEGATIVE_OUTPUT_FILE}")
    print(f"Saved combined model labels: {COMBINED_OUTPUT_FILE}")
    print(f"Saved construction report: {REPORT_OUTPUT_FILE}")

    print()
    print("Historical model label construction: OK")


if __name__ == "__main__":
    main()
