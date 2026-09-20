from src.etl.admin_spatial import (
    process_administrative_joins,
    save_administrative_join_outputs,
)


def main():

    print("Nairobi FloodWatch Administrative Spatial Joins")

    print("-" * 65)

    print("Assigning flood observations to administrative areas...")

    (
        administrative_data,
        inventory,
    ) = process_administrative_joins()

    print(f"Flood observations: {len(administrative_data):,}")

    print("\nBoundary inventory:")

    for row in inventory.itertuples(index=False):
        print(
            f"{row.admin_level}: "
            f"{row.nairobi_features:,} features "
            f"({row.file_name})"
        )

    print("\nCounty assignments:")

    county_counts = (administrative_data["county_name"].value_counts())

    for name, count in county_counts.items():
        print(f"{name}: {count:,}")

    print("\nSubcounty assignments:")

    subcounty_counts = (administrative_data["subcounty_name"].value_counts().sort_index())

    for name, count in subcounty_counts.items():
        print(f"{name}: {count:,}")

    print("\nWard assignments:")

    ward_counts = (administrative_data["ward_name"].value_counts().sort_index())

    print(f"Unique wards represented: {len(ward_counts):,}")

    for name, count in ward_counts.items():
        print(f"{name}: {count:,}")

    save_administrative_join_outputs(administrative_data, inventory)

    print("\nAdministrative spatial joins: OK")


if __name__ == "__main__":
    main()