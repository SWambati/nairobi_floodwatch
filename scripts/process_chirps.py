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

    
    # ARCHIVE VALIDATION
    

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

    
    # REAL NAIROBI EXTRACTION REQUESTS
    

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

    
    # RAINFALL EXTRACTION
    

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

    
    # SAMPLING METHOD SUMMARY
    

    print(
        "\nSampling methods:"
    )

    sampling_counts = (
        rainfall[
            "chirps_sampling_method"
        ]
        .value_counts(
            dropna=False
        )
    )

    for method, count in (
        sampling_counts.items()
    ):
        print(
            f"  {method}: "
            f"{count:,}"
        )

    fallback = rainfall[
        rainfall[
            "chirps_sampling_method"
        ] == "nearest_valid"
    ]

    if not fallback.empty:
        print(
            "\nFallback distance "
            "(CHIRPS pixels):"
        )

        fallback_counts = (
            fallback[
                "chirps_fallback_distance_pixels"
            ]
            .value_counts()
            .sort_index()
        )

        for distance, count in (
            fallback_counts.items()
        ):
            print(
                f"  {distance} pixel(s): "
                f"{count:,}"
            )

    
    # RAINFALL SUMMARY
    

    if valid_count > 0:
        print(
            "\nRainfall statistics:"
        )

        print(
            "  Range: "
            f"{rainfall['rainfall_mm'].min():.2f} "
            "to "
            f"{rainfall['rainfall_mm'].max():.2f} mm"
        )

        print(
            "  Median: "
            f"{rainfall['rainfall_mm'].median():.2f} mm"
        )

        print(
            "  Mean: "
            f"{rainfall['rainfall_mm'].mean():.2f} mm"
        )

    
    # SAVE OUTPUTS
    

    save_chirps_outputs(
        inventory,
        rainfall,
    )

    
    # FINAL VALIDATION
    

    if missing_count > 0:
        missing_records = rainfall[
            rainfall[
                "rainfall_mm"
            ].isna()
        ]

        print(
            "\nWARNING:"
        )

        print(
            f"{missing_count:,} rainfall "
            "observations remain missing even "
            "after nearest-valid-cell fallback."
        )

        print(
            "Review these records before using "
            "the output for feature engineering."
        )

        print(
            "\nMissing coordinates:"
        )

        print(
            missing_records[
                [
                    "label_record_id",
                    "date",
                    "latitude",
                    "longitude",
                ]
            ].to_string(
                index=False
            )
        )

        raise RuntimeError(
            "CHIRPS rainfall extraction completed "
            "with unresolved missing observations."
        )

    print(
        "\nCHIRPS rainfall extraction: OK"
    )


if __name__ == "__main__":
    main()