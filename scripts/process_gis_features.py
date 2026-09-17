from src.etl.gis_features import (process_gis_features, save_gis_feature_outputs)

def main():
    print(
        "Nairobi FloodWatch GIS Environmental Feature Extraction")

    print("-" * 70)

    print("Extracting static and historical environmental features...")

    (features, inventory) = process_gis_features()

    print(f"Flood observations: {len(features):,}")
    print(f"Raster sources: {len(inventory):,}")
    print("\nEnvironmental feature summary:")

    feature_columns = [
        "elevation_m",
        "slope_deg",
        "flood_hazard_score",
        "ghsl_built_surface_m2",
        "ghsl_built_up_fraction",
    ]

    for feature in feature_columns:
        series = features[feature]

        print(f"\n{feature}")
        print(f"min: {series.min():.2f}")
        print(f"max: {series.max():.2f}")
        print(f"mean: {series.mean():.2f}")
        print(f"median: {series.median():.2f}")

    print("\nGHSL epoch usage:")

    epoch_counts = (features["ghsl_epoch"].value_counts().sort_index())

    for epoch, count in epoch_counts.items():
        print(f"{epoch}: {count:,}")

    print("\nWorldCover classes:")

    class_counts = (
        features["worldcover_2021_class"].astype(int).value_counts().sort_index())

    for class_value, count in class_counts.items():
        print(f"{class_value}: {count:,}")

    print("\nSampling methods:")

    sampling_columns = [
        "elevation_m_sampling_method",
        "slope_deg_sampling_method",
        "flood_hazard_score_sampling_method",
        "worldcover_2021_class_sampling_method",
        "ghsl_sampling_method",
    ]

    for column in sampling_columns:
        print(f"\n{column}:")
        counts = (features[column].value_counts(dropna=False))

        for method, count in counts.items():
            print(f"  {method}: {count:,}")

    save_gis_feature_outputs(features, inventory)

    print("\nGIS environmental feature extraction: OK")

if __name__ == "__main__":
    main()