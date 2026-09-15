from src.etl.era5_features import (
    process_era5_features,
    save_era5_feature_outputs,
)


def main():
    print("Nairobi FloodWatch ERA5-Land Meteorological Feature Engineering")

    print("-" * 70)

    print("Deriving meteorological variables and antecedent features...")

    (
        antecedent_hourly,
        features
    ) = process_era5_features()

    #summary
    event_count = len(features)
    hourly_count = len(antecedent_hourly)
    expected_hourly_count = event_count * 24

    print(f"Flood observations: {event_count:,}")
    print(f"Antecedent hourly observations: {hourly_count:,}")
    print(f"Expected hourly observations: {expected_hourly_count:,}")

    if hourly_count != expected_hourly_count:
        raise RuntimeError(
            "Unexpected number of ERA5 antecedent hourly observations"
        )

    #feature summary
    print("\nMeteorological feature summary:")

    feature_columns = [
        "temperature_mean_c",
        "temperature_min_c",
        "temperature_max_c",
        "relative_humidity_mean_pct",
        "pressure_mean_hpa",
        "era5_precip_1d_mm",
        "soil_moisture_layer1_mean",
        "soil_moisture_layer2_mean",
    ]

    for feature in feature_columns:

        series = features[feature]

        print(f"\n{feature}")
        print(f"min: {series.min():.2f}")
        print(f"max: {series.max():.2f}")
        print(f"mean: {series.mean():.2f}")
        print(f"median: {series.median():.2f}")

    #distance summary
    distances = features["era5_distance_km"]

    print("\nERA5 grid distance:")
    print(f"median: {distances.median():.2f} km")
    print(f"90th percentile: {distances.quantile(0.90):.2f} km")
    print(f"max: {distances.max():.2f} km")

    #save
    save_era5_feature_outputs(
        antecedent_hourly,
        features
    )

    print("\nERA5-Land meteorological feature engineering: OK")


if __name__ == "__main__":
    main()