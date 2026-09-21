from sqlalchemy import func
from sqlalchemy import select

from src.etl.postgres_loader import (
    SessionLocal,
    load_etl_outputs_to_postgres,
    save_postgres_load_report,
)

from src.database.models import (
    DataSource,
    EnvironmentalFeature,
    FloodEvent,
    Location,
    WeatherData,
)


def main():

    print(
        "Nairobi FloodWatch PostgreSQL "
        "ETL Loading Layer"
    )

    print("-" * 65)

    print(
        "Loading validated ETL outputs "
        "into PostgreSQL..."
    )

    report = (
        load_etl_outputs_to_postgres()
    )

    print(
        "\nLoad summary:"
    )

    for row in report.fillna(
        "-"
    ).itertuples(
        index=False
    ):

        values = [
            f"component={row.component}",
            f"processed={row.processed}",
        ]

        if hasattr(
            row,
            "inserted"
        ):
            values.append(
                f"inserted={row.inserted}"
            )

        if hasattr(
            row,
            "updated"
        ):
            values.append(
                f"updated={row.updated}"
            )

        print(
            " | ".join(
                values
            )
        )

    #database verification
    session = SessionLocal()

    try:

        table_models = [
            (
                "data_source",
                DataSource,
            ),
            (
                "location",
                Location,
            ),
            (
                "weather_data",
                WeatherData,
            ),
            (
                "flood_event",
                FloodEvent,
            ),
            (
                "environmental_feature",
                EnvironmentalFeature,
            ),
        ]

        print(
            "\nDatabase row counts:"
        )

        for (
            table_name,
            model,
        ) in table_models:

            count = session.execute(
                select(
                    func.count()
                ).select_from(
                    model
                )
            ).scalar_one()

            print(
                f"{table_name}: "
                f"{count:,}"
            )

    finally:

        session.close()

    save_postgres_load_report(
        report
    )

    print(
        "\nPostgreSQL ETL loading: OK"
    )


if __name__ == "__main__":
    main()