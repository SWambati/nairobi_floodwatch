from src.etl.flood_labels import ( process_flood_labels, save_flood_label_outputs, )
def main():
    print(
        "Nairobi FloodWatch"
        "Flood Label Preprocessing"
    )
    print("=" * 50)
    (
        groundsource_labels,
        master_event_anchors,
    ) = process_flood_labels()

    print("\nProcessing Summary")
    print("-" * 50)
    print(
    "Corroborated Groundsource candidates: "
    f"{(
        groundsource_labels[
            'verification_status'
        ] == 'corroborated'
    ).sum():,}"
    )
    print(
    "Near-match Groundsource candidates: "
    f"{(
        groundsource_labels[
            'verification_status'
        ] == 'near_match'
    ).sum():,}"
    )
    print(
    "Groundsource-only candidates: "
    f"{(
        groundsource_labels[
            'verification_status'
        ] == 'candidate'
    ).sum():,}"
    )

    save_flood_label_outputs(
        groundsource_labels,
        master_event_anchors,
    )

    print("\nFlood-label preprocessing: OK")

if __name__ == "__main__":
    main()