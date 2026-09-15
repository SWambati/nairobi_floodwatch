from src.etl.era5_land import (ALL_ERA5_VARIABLES, EXPECTED_HOURS_PER_GRID_POINT, EXPECTED_TOTAL_ROWS, process_era5_land, save_era5_outputs)

def main():
    print("Nairobi FloodWatch ERA-5 Land Data Processing")

    print("-" * 60)

    print("Loading and validating ERA-Land parameter files...")
    (
        era5_data,
        grid_mapping,
        inventory
    ) = process_era5_land()

    #archive summary

    print(f"Hourly rows: {len(era5_data):,}")
    print(f"Expected hourly rows: {EXPECTED_TOTAL_ROWS:,}")

    grid_points = (era5_data[["latitude", "longitude"]].drop_duplicates())

    print(f"ERA5 grid points: {len(grid_points):,}")
    print(f"Hours per grid point: {EXPECTED_HOURS_PER_GRID_POINT:,}")
    print(f"Coverage: {era5_data['timestamp'].min()} to {era5_data['timestamp'].max()}")

    #missing variables

    print("Missing variables:")

    for variable in sorted(ALL_ERA5_VARIABLES):
        missing = int(era5_data[variable].isna().sum())
        print(f"{variable}: {missing:,}")

    #source inventory
    print("Parameter files: ")

    for row in inventory.itertuples(index=False):
        print(f"{row.paramter_group}: {row.file_name}")

    #grid-mapping summary
    print(f"Groundsource locations mapped: {len(grid_mapping):,}")

    distances = (grid_mapping["era5_distance_km"])
    print("Nearest ERA5 distance:")
    print(f"Median: {distances.median():.2f} km")
    print(f"Max: {distances.max():.2f} km")
    print(f"90th percentile: {distances.quantile(0.9):.2f} km")

    #save
    save_era5_outputs(era5_data, grid_mapping, inventory)
    print("ERA5-Land preprocessing: OK")

if __name__ == "__main__":
    main()
