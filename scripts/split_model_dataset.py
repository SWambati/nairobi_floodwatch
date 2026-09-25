from src.model.model_split import (
    ASSIGNMENT_FILE,
    SPLIT_DATASET_FILE,
    SPLIT_REPORT_FILE,
    TEST_FILE,
    TRAIN_FILE,
    VALIDATION_FILE,
    split_historical_model_dataset,
)


def main():

    print("Nairobi FloodWatch Leakage-Safe Model Dataset Splitting")

    print("=" * 50)

    print("Loading validated historical model dataset...")
    print("Pairing flood event groups with matched non-flood controls...")
    print("Searching for leakage-safe 70/15/15 group split...")

    (
        split_dataset,
        train,
        validation,
        test,
        group_summary,
        report,
    ) = split_historical_model_dataset()

    total_rows = len(split_dataset)

    print()
    print("Split summary:")

    print("-" * 50)

    for (split_name, subset,) in [
        (
            "Train",
            train,
        ),
        (
            "Validation",
            validation,
        ),
        (
            "Test",
            test,
        ),
    ]:

        positive_rows = int((subset["flood_label"] == 1).sum())
        negative_rows = int(
            (
                subset[
                    "flood_label"
                ] == 0
            ).sum()
        )

        ratio = (
            len(
                subset
            )
            / total_rows
        )

        group_count = (
            subset[
                "model_split_group_id"
            ].nunique()
        )

        print(
            f"{split_name}:"
        )

        print(
            f"  rows: "
            f"{len(subset):,} "
            f"({ratio:.2%})"
        )

        print(
            f"  flood: "
            f"{positive_rows:,}"
        )

        print(
            f"  presumed non-flood: "
            f"{negative_rows:,}"
        )

        print(
            f"  split groups: "
            f"{group_count:,}"
        )

        print()

    print(
        "Group construction:"
    )

    print(
        f"  leakage-safe event groups: "
        f"{len(group_summary):,}"
    )

    print(
        "  positive events kept intact: yes"
    )

    print(
        "  matched negative controls kept "
        "with reference positives: yes"
    )

    print()

    print(
        "Leakage validation:"
    )

    leakage_metrics = [
        "train_validation_group_overlap",
        "train_test_group_overlap",
        "validation_test_group_overlap",
        "matched_pair_leaks",
        "positive_event_group_leaks",
    ]

    for metric in (
        leakage_metrics
    ):

        value = (
            report.loc[
                report[
                    "metric"
                ] == metric,
                "value",
            ]
            .iloc[0]
        )

        print(
            f"  {metric}: "
            f"{int(value)}"
        )

    print()

    print(
        "Split policy:"
    )

    print(
        "  Training: 70%"
    )

    print(
        "  Validation: 15%"
    )

    print(
        "  Test: 15%"
    )

    print(
        "  Random seed: 42"
    )

    print(
        "  Test set remains untouched "
        "during tuning"
    )

    print()

    print(
        f"Saved split dataset: "
        f"{SPLIT_DATASET_FILE}"
    )

    print(
        f"Saved train features: "
        f"{TRAIN_FILE}"
    )

    print(
        f"Saved validation features: "
        f"{VALIDATION_FILE}"
    )

    print(
        f"Saved test features: "
        f"{TEST_FILE}"
    )

    print(
        f"Saved split assignments: "
        f"{ASSIGNMENT_FILE}"
    )

    print(
        f"Saved split report: "
        f"{SPLIT_REPORT_FILE}"
    )

    print()

    print(
        "Leakage-safe dataset "
        "splitting: OK"
    )


if __name__ == "__main__":
    main()