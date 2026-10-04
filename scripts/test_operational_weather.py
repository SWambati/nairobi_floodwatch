import pandas as pd
from src.acquisition.open_meteo import (OpenMeteoClient, save_open_meteo_snapshot)
from src.operational.runtime_features import (build_weather_features)


def main():
    print("Nairobi FloodWatch Operational Weather Test")
    print("=" * 50)

    locations = pd.DataFrame([
        {
            "location_id": "NAIROBI_TEST",
            "latitude": -1.286389,
            "longitude": 36.817223,
        }
    ])

    print("Fetching near-real-time Open-Meteo data...")

    client = OpenMeteoClient()
    batch = client.fetch_locations(locations)
    files = save_open_meteo_snapshot(batch)

    print()
    print("API acquisition:")
    print(f"Generic hourly rows: {len(batch['generic_hourly']):,}")
    print(f"ECMWF hourly rows: {len(batch['ecmwf_hourly']):,}")
    print(f"Current rows: {len(batch['current']):,}")
    print()

    current = batch["current"].iloc[0]

    print("Current Nairobi conditions:")
    print(f"Temperature: {current['temperature_2m']}")
    print(f"Relative humidity: {current['relative_humidity_2m']}")
    print(f"Precipitation: {current['precipitation']}")
    print(f"Surface pressure: {current['surface_pressure']}")
    print()

    features = build_weather_features(batch["generic_hourly"], batch["ecmwf_hourly"])

    row = features.iloc[0]

    print("Operational weather predictors:")
    print("-" * 50)

    for column in features.columns:
        if column not in {"location_id", "prediction_date"}:
            print(f"{column:<34} {row[column]:.4f}")

    print()

    print(f"Prediction date: {row['prediction_date']}")
    print()

    print("Saved snapshots:")
    for name, path in files.items():
        print(f"{name:<18} {path}")

    print()

    print("Open-Meteo acquisition: OK")
    
    print("Operational weather feature generation: OK")


if __name__ == "__main__":
    main()