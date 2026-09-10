from src.etl.chirps import (
    extract_daily_rainfall,
    load_groundsource_event_requests,
    save_chirps_outputs,
    validate_chirps_archive,
)


def main():
    print(
        "Nairobi FloodWatch CHIRPS "
        "Rainfall Extraction"
    )

    print("=" * 60)


    # Validate acquired CHIRPS archive


    print(
        "Validating CHIRPS archive..."
    )

    inventory = (
        validate_chirps_archive()
    )

    total_bands = int(
        inventory[
            "band_count"
        ].sum()
    )

    print(
        f"Annual raster files: "
        f"{len(inventory):,}"
    )

    print(
        f"Daily rainfall bands: "
        f"{total_bands:,}"
    )

    print(
        f"Coverage: "
        f"{inventory['first_date'].min()} "
        f"to "
        f"{inventory['last_date'].max()}"
    )


    # Build real Nairobi extraction requests


    print(
        "\nLoading Groundsource "
        "event-date requests..."
    )

    requests = (
        load_groundsource_event_requests()
    )

    print(
        f"Rainfall requests: "
        f"{len(requests):,}"
    )


    # Extract rainfall
     

    print(
        "\nExtracting CHIRPS rainfall..."
    )

    rainfall = (
        extract_daily_rainfall(
            requests
        )
    )

    valid_count = int(
        rainfall[
            "rainfall_mm"
        ].notna().sum()
    )

    missing_count = int(
        rainfall[
            "rainfall_mm"
        ].isna().sum()
    )

    print(
        f"Valid rainfall observations: "
        f"{valid_count:,}"
    )

    print(
        f"Missing rainfall observations: "
        f"{missing_count:,}"
    )

    if valid_count > 0:
        print(
            "Rainfall range: "
            f"{rainfall['rainfall_mm'].min():.2f} "
            "to "
            f"{rainfall['rainfall_mm'].max():.2f} mm"
        )

        print(
            "Median rainfall: "
            f"{rainfall['rainfall_mm'].median():.2f} mm"
        )


    # Persist outputs


    save_chirps_outputs(
        inventory,
        rainfall,
    )

    print(
        "\nCHIRPS rainfall extraction: OK"
    )


if __name__ == "__main__":
    main()