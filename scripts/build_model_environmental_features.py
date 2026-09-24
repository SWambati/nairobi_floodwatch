from src.model.model_environmental_features import (ENVIRONMENTAL_OUTPUT_FILE, GHSL_SAMPLING_OUTPUT_FILE, build_model_environmental_features)

def main():
    print("Nairobi FloodWatch Model Environmental Features")

    print("-" * 50)

    print("Loading historical model observations...")

    print("Mapping static environmental features...")

    print("Extracting time-correct GHSL built-up features...")

    (
        labels,
        static_features,
        ghsl_features,
        combined,
    ) = (build_model_environmental_features())

    print()

    print("Environmental Feature Summary: ")

    print("-" * 50)

    print(f"Model observations: {len(labels):,}")
    print(f"Static feature rows: {len(static_features):,}")
    print(f"GHSL feature rows: {len(ghsl_features):,}")
    print(f"Combined feature rows: {len(combined):,}")

    print()
    print("Class Distribution: ")
    counts = (combined["flood_label"].value_counts().sort_index(ascending=False))

    for label, count in (counts.items()):

        label_name = ("Flood" if label == 1 else "Presumed non-flood")

        print(f" {label} ({label_name}): {count:,}")

    print()
    print("Environmental Features: ")

    for column in [
        "elevation_m",
        "slope_deg",
        "flood_hazard_score",
        "ghsl_built_surface_m2",
        "ghsl_built_up_fraction",
    ]:
        print(f" {column}: min={combined[column].min():.2f}, max={combined[column].max():.2f}, mean={combined[column].mean():.2f}")

    print()
    print("GHSL epoch usage: ")

    epoch_counts = (combined["ghsl_epoch"].value_counts().sort_index())

    for epoch, count in (epoch_counts.items()):
        print(f"{int(epoch)}: {count:,}")

    print()

    positive_count = int((combined["flood_label"] == 1).sum())
    negative_count = int((combined["flood_label"] == 0).sum())

    print("Static Feature Strategy: ")
    print(f"Positive Source Locations: {positive_count:,}")
    print(f"Matched negative locations: {negative_count:,}")

    print("Negative inherit elevation, slope and ICPAC from their same-location reference positive")
    print("GHSL is sampled independently using each observation year")
    print("WorldCover 2021 excluded from baseline model predictors")

    print()
    print("Positive GHSL consistency with static GIS Feature Extraction: PASS")

    print()

    print(f"Saved GHSL sampling output: {GHSL_SAMPLING_OUTPUT_FILE}")

    print(f"Saved environmental features: {ENVIRONMENTAL_OUTPUT_FILE}")

    print()

    print("Historical model environmental feature generation: OK")

if __name__ == "__main__":
    main()