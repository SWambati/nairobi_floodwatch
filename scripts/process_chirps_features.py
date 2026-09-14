from src.etl.chirps_features import (MAX_LOOKBACK_DAYS, process_chirps_features, save_chirps_feature_outputs)

def main():
    print("Nairobi FloodWatch CHIRPS Rainfall Feature Engineering")

    print("=" * 50)
    print("Feature Definition: ")
    print(" rain_1d = D-1")
    print(" rain_3d = sum of rainfall from D-3 to D-1")
    print(" rain_7d = sum of rainfall from D-7 to D-1")

    print(" Event day D is excluded from baseline rainfall predictors")

    #process
    print("\n Building antecedent rainfall requests ...")
    (
        daily_rainfall,
        rainfall_features,
    ) = process_chirps_features()

    event_count = len(rainfall_features)
    daily_request_count = len(daily_rainfall)

    expected_daily_requests = (event_count * MAX_LOOKBACK_DAYS)

    print(f"Flood Observations: {event_count:,}")
    print(f"Daily Rainfall Requests: {daily_request_count:,}")
    print(f"Expected Daily Requests: {expected_daily_requests:,}")

    if (daily_request_count != expected_daily_requests):
        raise RuntimeError("Unexpected number of antecedent rainfall observations")

    #missing values

    valid_daily = int(daily_rainfall["rainfall_mm"].notna().sum())
    missing_daily = int(daily_rainfall["rainfall_mm"].isna().sum())

    print(f"Valid Daily Rainfall Requests: {valid_daily:,}")
    print(f"Missing Daily Rainfall Requests: {missing_daily:,}")

    if missing_daily > 0:
        raise RuntimeError("Cannot continue: antecedent. CHIRPS rainfall data contains missing values")

    #sampling provenance

    print("\nDaily Sampling Methods: ")

    sampling_counts = (daily_rainfall["chirps_sampling_method"].value_counts(dropna=False))

    for method, count in(sampling_counts.items()):
        print(f"  {method}: {count:,}")

    fallback = daily_rainfall[daily_rainfall["chirps_sampling_method"]== "nearest_valid"]

    if not fallback.empty:
        print("\nFallback distance "
              "(CHIRPS pixels):")

        fallback_counts = (fallback["chirps_fallback_distance_pixels"].value_counts().sort_index())

        for distance, count in fallback_counts.items():
            print(f"  {distance} pixels: {count:,}")

    #feature summary

    print("\nRainfall feature summmary: ")

    for feature in ["rain_1d", "rain_3d", "rain_7d"]:
        series = rainfall_features[feature]

        print(f"\n {feature}")

        print(f" min: {series.min():.2f} mm")
        print(f" max: {series.max():.2f} mm")
        print(f" mean: {series.mean():.2f} mm")
        print(f" median: {series.median():.2f} mm")


    #consistency check

    accumulation_valid = (
        (
            rainfall_features["rain_1d"] <= rainfall_features["rain_3d"]
        ) &
        (
            rainfall_features["rain_3d"] <= rainfall_features["rain_7d"]
        )
    )
    print(" rain_1d <= rain_3d <= rain_7d: "
          f"{accumulation_valid.all()}")

    if not accumulation_valid.all():
        raise RuntimeError("Rainfall accumulation consistency validation failed")


    #save
    save_chirps_feature_outputs(daily_rainfall, rainfall_features)

    print("\nCHIRPS rainfall feature engineering: OK")


if __name__ == "__main__":
    main()