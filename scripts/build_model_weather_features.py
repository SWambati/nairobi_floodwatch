from src.model.model_weather_features import (CHIRPS_OUTPUT_FILE, ERA5_OUTPUT_FILE, WEATHER_OUTPUT_FILE, build_model_weather_features)

def main():
    print("Nairobi FloodWatch Historical Model Weather Features")

    print("-" * 50)

    print("Loading 1494 historical model observations...")

    print("Extracting CHIRPS antecedent rainfall...")

    print("Deriving ERA5-Land antecedent meteorology...")

    (labels,
     chirps,
     era5,
     combined,
     chirps_daily,
     era5_hourly,
    ) = build_model_weather_features()

    print()

    print("Weather feature summary: ")
    print(f"Model observations: {len(labels):,}")
    print(f"CHIRPS feature rows: {len(chirps):,}")
    print(f"ERA5 feature rows: {len(era5):,}")
    print(f"Combined weather rows: {len(combined):,}")

    print()
    print("Class Distribution")

    counts = (combined["flood_label"].value_counts().sort_index(ascending=False))

    for label, count in (counts.items()):
        name = ("Flood" if label == 1 else "Presumed non-flood")

        print(f" {label} ({name}) {count:,}")

    print()
    print("CHIRPS Rainfall: ")

    for column in ["rain_1d", "rain_3d", "rain_7d"]:
        print(f" {column}: min={combined[column].min():.2f}, max={combined[column].max():.2f}, mean={combined[column].mean():.2f}")

    print()

    print("ERA5 grid distance: ")

    print(f"median: {combined['era5_grid_distance_km'].median():.2f} km")
    print(f"90th percentile: {combined['era5_grid_distance_km'].quantile(0.90):.2f} km")
    print(f"max: {combined['era5_grid_distance_km'].max():.2f} km")

    print()

    print(f"Saved CHIRPS model features: {CHIRPS_OUTPUT_FILE}")
    print(f"Saved ERA5 model features: {ERA5_OUTPUT_FILE}")
    print(f"Saved combined weather features: {WEATHER_OUTPUT_FILE}")

    print()

    print("Historical model weather feature generation: OK")

if __name__ == "__main__":
    main()



