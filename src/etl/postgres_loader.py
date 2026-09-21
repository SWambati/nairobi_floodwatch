from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from config.paths import (
    FEATURES_DIR,
    PROCESSED_DIR,
)

from config.settings import DATABASE_URL

from src.database.models import (
    DataSource,
    EnvironmentalFeature,
    FloodEvent,
    Location,
    WeatherData,
)


#configuration
DATABASE_OUTPUT_DIR = (
    PROCESSED_DIR
    / "database"
)

LABEL_FILE = (
    PROCESSED_DIR
    / "flood_labels"
    / "groundsource_positive_candidates.csv"
)

CHIRPS_DAILY_FILE = (
    PROCESSED_DIR
    / "weather"
    / "chirps"
    / "groundsource_antecedent_daily_rainfall.csv"
)

ERA5_HOURLY_FILE = (
    PROCESSED_DIR
    / "weather"
    / "era5_land"
    / "groundsource_era5_antecedent_hourly.parquet"
)

GIS_FEATURE_FILE = (
    FEATURES_DIR
    / "environmental"
    / "groundsource_environmental_features.csv"
)

ADMIN_JOIN_FILE = (
    PROCESSED_DIR
    / "spatial"
    / "administrative"
    / "groundsource_administrative_joins.csv"
)

NAIROBI_TIMEZONE = ZoneInfo(
    "Africa/Nairobi"
)

UTC_TIMEZONE = ZoneInfo(
    "UTC"
)

WEATHER_CHUNK_SIZE = 1000


#database setup
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


#utility functions
def safe_float(value):

    if pd.isna(value):
        return None

    return float(value)


def coordinate_key(
    latitude,
    longitude,
) -> tuple[float, float]:

    return (
        round(
            float(latitude),
            6,
        ),
        round(
            float(longitude),
            6,
        ),
    )


def local_date_to_utc(
    value,
):

    if pd.isna(value):
        return None

    timestamp = pd.Timestamp(
        value
    )

    timestamp = timestamp.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    timestamp = timestamp.tz_localize(
        NAIROBI_TIMEZONE
    )

    return (
        timestamp
        .tz_convert(
            UTC_TIMEZONE
        )
        .to_pydatetime()
    )


#input validation
def validate_loading_inputs() -> None:

    required_files = [
        LABEL_FILE,
        CHIRPS_DAILY_FILE,
        ERA5_HOURLY_FILE,
        GIS_FEATURE_FILE,
        ADMIN_JOIN_FILE,
    ]

    missing = [
        str(path)
        for path in required_files
        if not path.exists()
    ]

    if missing:
        raise FileNotFoundError(
            "Required ETL outputs are missing:\n"
            + "\n".join(
                missing
            )
        )


#load source files
def load_etl_outputs() -> dict[str, pd.DataFrame]:

    return {
        "labels":
            pd.read_csv(
                LABEL_FILE
            ),

        "chirps_daily":
            pd.read_csv(
                CHIRPS_DAILY_FILE
            ),

        "era5_hourly":
            pd.read_parquet(
                ERA5_HOURLY_FILE
            ),

        "gis_features":
            pd.read_csv(
                GIS_FEATURE_FILE
            ),

        "admin_joins":
            pd.read_csv(
                ADMIN_JOIN_FILE
            ),
    }


#data-source registry
SOURCE_DEFINITIONS = {
    "groundsource": {
        "name":
            "Groundsource",

        "source_type":
            "historical_flood_labels",

        "description":
            "Historical Nairobi flood observations "
            "derived from Groundsource.",

        "update_frequency":
            "static",
    },

    "chirps": {
        "name":
            "CHIRPS v3 Daily SAT",

        "source_type":
            "satellite_rainfall",

        "description":
            "Historical daily precipitation used "
            "for rainfall feature generation.",

        "update_frequency":
            "daily",
    },

    "era5_land": {
        "name":
            "ERA5-Land",

        "source_type":
            "reanalysis_weather",

        "description":
            "Historical hourly meteorological "
            "reanalysis data.",

        "update_frequency":
            "hourly",
    },

    "nasadem": {
        "name":
            "NASADEM",

        "source_type":
            "terrain",

        "description":
            "Elevation and derived slope data.",

        "update_frequency":
            "static",
    },

    "icpac": {
        "name":
            "ICPAC 100-year Flood Inundation",

        "source_type":
            "flood_hazard",

        "description":
            "Static fluvial flood inundation "
            "model-agreement layer.",

        "update_frequency":
            "static",
    },

    "ghsl": {
        "name":
            "GHSL Built Surface",

        "source_type":
            "built_environment",

        "description":
            "Historical built-surface observations "
            "at five-year epochs.",

        "update_frequency":
            "5-year",
    },

    "worldcover": {
        "name":
            "ESA WorldCover 2021",

        "source_type":
            "land_cover",

        "description":
            "2021 land-cover classification.",

        "update_frequency":
            "static",
    },
}


def ensure_data_sources(
    session,
) -> dict[str, DataSource]:

    sources = {}

    for (
        key,
        definition,
    ) in SOURCE_DEFINITIONS.items():

        existing = session.execute(
            select(
                DataSource
            ).where(
                DataSource.name
                == definition["name"]
            )
        ).scalar_one_or_none()

        if existing is None:

            existing = DataSource(
                name=
                    definition["name"],

                source_type=
                    definition[
                        "source_type"
                    ],

                description=
                    definition[
                        "description"
                    ],

                source_url=None,

                update_frequency=
                    definition[
                        "update_frequency"
                    ],

                is_active=True,
            )

            session.add(
                existing
            )

            session.flush()

        else:

            existing.source_type = (
                definition[
                    "source_type"
                ]
            )

            existing.description = (
                definition[
                    "description"
                ]
            )

            existing.update_frequency = (
                definition[
                    "update_frequency"
                ]
            )

            existing.is_active = True

        sources[key] = existing

    return sources


#location loading
def ensure_locations(
    session,
    admin_joins: pd.DataFrame,
) -> dict[tuple[float, float], int]:

    existing_locations = (
        session.execute(
            select(
                Location
            )
        )
        .scalars()
        .all()
    )

    location_map = {}

    for location in existing_locations:

        key = coordinate_key(
            location.latitude,
            location.longitude,
        )

        if key not in location_map:
            location_map[
                key
            ] = location.location_id

    unique_locations = (
        admin_joins[
            [
                "latitude",
                "longitude",
                "county_name",
                "subcounty_name",
                "ward_name",
            ]
        ]
        .drop_duplicates(
            subset=[
                "latitude",
                "longitude",
            ]
        )
    )

    inserted = 0

    for row in unique_locations.itertuples(
        index=False
    ):

        key = coordinate_key(
            row.latitude,
            row.longitude,
        )

        if key in location_map:
            continue

        location = Location(
            name=(
                f"{row.ward_name}, "
                f"{row.subcounty_name}"
            ),

            administrative_level=
                "observation_point",

            latitude=
                key[0],

            longitude=
                key[1],
        )

        session.add(
            location
        )

        session.flush()

        location_map[
            key
        ] = location.location_id

        inserted += 1

    return (
        location_map,
        inserted,
    )


#flood-event loading
def load_flood_events(
    session,
    labels: pd.DataFrame,
    location_map,
    groundsource_source,
) -> dict[str, int]:

    existing_events = (
        session.execute(
            select(
                FloodEvent
            ).where(
                FloodEvent.source_id
                == groundsource_source.source_id
            )
        )
        .scalars()
        .all()
    )

    event_map = {
        event.source_event_id:
            event
        for event in existing_events
        if event.source_event_id
        is not None
    }

    inserted = 0
    updated = 0

    for row in labels.itertuples(
        index=False
    ):

        key = coordinate_key(
            row.latitude,
            row.longitude,
        )

        location_id = (
            location_map[
                key
            ]
        )

        source_event_id = str(
            row.label_record_id
        )

        start_time = (
            local_date_to_utc(
                row.start_date
            )
        )

        end_time = (
            local_date_to_utc(
                row.end_date
            )
        )

        description = (
            "Groundsource historical flood "
            f"observation. Event group: "
            f"{row.event_group_id}. "
            "Source timestamps are date-level."
        )

        if source_event_id in event_map:

            event = event_map[
                source_event_id
            ]

            event.location_id = (
                location_id
            )

            event.event_type = (
                "flood"
            )

            event.title = (
                "Historical flood observation"
            )

            event.description = (
                description
            )

            event.start_time = (
                start_time
            )

            event.end_time = (
                end_time
            )

            event.severity_level = None

            event.verification_status = (
                row.verification_status
            )

            updated += 1

        else:

            event = FloodEvent(
                location_id=
                    location_id,

                source_id=
                    groundsource_source.source_id,

                source_event_id=
                    source_event_id,

                event_type=
                    "flood",

                title=
                    "Historical flood observation",

                description=
                    description,

                start_time=
                    start_time,

                end_time=
                    end_time,

                severity_level=None,

                verification_status=
                    row.verification_status,
            )

            session.add(
                event
            )

            event_map[
                source_event_id
            ] = event

            inserted += 1

    session.flush()

    return {
        "inserted":
            inserted,

        "updated":
            updated,
    }


#weather upsert helper
def execute_weather_upsert(
    session,
    records: list[dict],
    update_columns: list[str],
) -> int:

    if not records:
        return 0

    processed = 0

    for start in range(
        0,
        len(records),
        WEATHER_CHUNK_SIZE,
    ):

        chunk = records[
            start:
            start + WEATHER_CHUNK_SIZE
        ]

        statement = (
            pg_insert(
                WeatherData
            )
            .values(
                chunk
            )
        )

        update_values = {
            column:
                getattr(
                    statement.excluded,
                    column,
                )
            for column
            in update_columns
        }

        statement = (
            statement
            .on_conflict_do_update(
                index_elements=[
                    "location_id",
                    "source_id",
                    "timestamp",
                ],
                set_=update_values,
            )
        )

        session.execute(
            statement
        )

        processed += len(
            chunk
        )

    return processed


#CHIRPS weather loading
def load_chirps_weather(
    session,
    chirps_daily: pd.DataFrame,
    location_map,
    chirps_source,
) -> int:

    records_by_key = {}

    for row in chirps_daily.itertuples(
        index=False
    ):

        coordinate = (
            coordinate_key(
                row.latitude,
                row.longitude,
            )
        )

        location_id = (
            location_map[
                coordinate
            ]
        )

        timestamp = (
            local_date_to_utc(
                row.date
            )
        )

        key = (
            location_id,
            chirps_source.source_id,
            timestamp,
        )

        rainfall = safe_float(
            row.rainfall_mm
        )

        if key in records_by_key:

            previous = (
                records_by_key[
                    key
                ]["rainfall"]
            )

            if (
                previous is not None
                and rainfall is not None
                and not np.isclose(
                    previous,
                    rainfall,
                    atol=1e-6,
                )
            ):
                raise ValueError(
                    "Conflicting CHIRPS rainfall "
                    "values detected for the same "
                    "location/date."
                )

            continue

        records_by_key[
            key
        ] = {
            "location_id":
                location_id,

            "source_id":
                chirps_source.source_id,

            "timestamp":
                timestamp,

            "temperature":
                None,

            "humidity":
                None,

            "rainfall":
                rainfall,

            "wind_speed":
                None,

            "pressure":
                None,

            "soil_moisture_layer1":
                None,

            "soil_moisture_layer2":
                None,
        }

    records = list(
        records_by_key.values()
    )

    return execute_weather_upsert(
        session,
        records,
        update_columns=[
            "rainfall",
        ],
    )


#ERA5 weather loading
def load_era5_weather(
    session,
    era5_hourly: pd.DataFrame,
    location_map,
    era5_source,
) -> int:

    records_by_key = {}

    for row in era5_hourly.itertuples(
        index=False
    ):

        coordinate = (
            coordinate_key(
                row.latitude,
                row.longitude,
            )
        )

        location_id = (
            location_map[
                coordinate
            ]
        )

        timestamp = pd.Timestamp(
            row.timestamp
        )

        if timestamp.tzinfo is None:
            timestamp = (
                timestamp.tz_localize(
                    UTC_TIMEZONE
                )
            )
        else:
            timestamp = (
                timestamp.tz_convert(
                    UTC_TIMEZONE
                )
            )

        timestamp = (
            timestamp
            .to_pydatetime()
        )

        key = (
            location_id,
            era5_source.source_id,
            timestamp,
        )

        record = {
            "location_id":
                location_id,

            "source_id":
                era5_source.source_id,

            "timestamp":
                timestamp,

            "temperature":
                safe_float(
                    row.temperature_c
                ),

            "humidity":
                safe_float(
                    row.relative_humidity_pct
                ),

            "rainfall":
                safe_float(
                    row.precipitation_mm
                ),

            "wind_speed":
                None,

            "pressure":
                safe_float(
                    row.pressure_hpa
                ),

            "soil_moisture_layer1":
                safe_float(
                    row.swvl1
                ),

            "soil_moisture_layer2":
                safe_float(
                    row.swvl2
                ),
        }

        if key not in records_by_key:
            records_by_key[
                key
            ] = record

    records = list(
        records_by_key.values()
    )

    return execute_weather_upsert(
        session,
        records,
        update_columns=[
            "temperature",
            "humidity",
            "rainfall",
            "pressure",
            "soil_moisture_layer1",
            "soil_moisture_layer2",
        ],
    )


#environmental feature loading
def load_environmental_features(
    session,
    gis_features: pd.DataFrame,
    location_map,
    sources,
) -> dict[str, int]:

    desired = {}

    for row in gis_features.itertuples(
        index=False
    ):

        coordinate = coordinate_key(
            row.latitude,
            row.longitude,
        )

        location_id = (
            location_map[
                coordinate
            ]
        )

        ghsl_date = date(
            int(
                row.ghsl_epoch
            ),
            1,
            1,
        )

        feature_definitions = [
            (
                sources["nasadem"].source_id,
                "elevation_m",
                safe_float(
                    row.elevation_m
                ),
                "m",
                None,
            ),
            (
                sources["nasadem"].source_id,
                "slope_deg",
                safe_float(
                    row.slope_deg
                ),
                "degrees",
                None,
            ),
            (
                sources["icpac"].source_id,
                "flood_hazard_score",
                safe_float(
                    row.flood_hazard_score
                ),
                "model_agreement",
                None,
            ),
            (
                sources["ghsl"].source_id,
                "ghsl_built_surface_m2",
                safe_float(
                    row.ghsl_built_surface_m2
                ),
                "m2",
                ghsl_date,
            ),
            (
                sources["ghsl"].source_id,
                "ghsl_built_up_fraction",
                safe_float(
                    row.ghsl_built_up_fraction
                ),
                "fraction",
                ghsl_date,
            ),
            (
                sources["worldcover"].source_id,
                "worldcover_2021_class",
                safe_float(
                    row.worldcover_2021_class
                ),
                "class_code",
                date(
                    2021,
                    1,
                    1,
                ),
            ),
        ]

        for (
            source_id,
            feature_name,
            feature_value,
            unit,
            effective_date,
        ) in feature_definitions:

            key = (
                location_id,
                source_id,
                feature_name,
                effective_date,
            )

            desired[
                key
            ] = {
                "location_id":
                    location_id,

                "source_id":
                    source_id,

                "feature_name":
                    feature_name,

                "feature_value":
                    feature_value,

                "unit":
                    unit,

                "effective_date":
                    effective_date,
            }

    managed_source_ids = [
        sources["nasadem"].source_id,
        sources["icpac"].source_id,
        sources["ghsl"].source_id,
        sources["worldcover"].source_id,
    ]

    existing_features = (
        session.execute(
            select(
                EnvironmentalFeature
            ).where(
                EnvironmentalFeature.source_id
                .in_(
                    managed_source_ids
                )
            )
        )
        .scalars()
        .all()
    )

    existing_map = {
        (
            feature.location_id,
            feature.source_id,
            feature.feature_name,
            feature.effective_date,
        ):
            feature

        for feature
        in existing_features
    }

    inserted = 0
    updated = 0

    for key, values in desired.items():

        if key in existing_map:

            feature = (
                existing_map[
                    key
                ]
            )

            feature.feature_value = (
                values[
                    "feature_value"
                ]
            )

            feature.unit = (
                values[
                    "unit"
                ]
            )

            updated += 1

        else:

            feature = (
                EnvironmentalFeature(
                    **values
                )
            )

            session.add(
                feature
            )

            inserted += 1

    session.flush()

    return {
        "inserted":
            inserted,

        "updated":
            updated,

        "total":
            len(
                desired
            ),
    }


#main loader
def load_etl_outputs_to_postgres() -> pd.DataFrame:

    validate_loading_inputs()

    datasets = (
        load_etl_outputs()
    )

    results = []

    session = SessionLocal()

    try:

        sources = (
            ensure_data_sources(
                session
            )
        )

        results.append(
            {
                "component":
                    "data_sources",

                "processed":
                    len(
                        sources
                    ),
            }
        )

        (
            location_map,
            inserted_locations,
        ) = ensure_locations(
            session,
            datasets[
                "admin_joins"
            ],
        )

        results.append(
            {
                "component":
                    "locations",

                "processed":
                    len(
                        location_map
                    ),

                "inserted":
                    inserted_locations,
            }
        )

        flood_result = (
            load_flood_events(
                session,
                datasets[
                    "labels"
                ],
                location_map,
                sources[
                    "groundsource"
                ],
            )
        )

        results.append(
            {
                "component":
                    "flood_events",

                "processed":
                    len(
                        datasets[
                            "labels"
                        ]
                    ),

                "inserted":
                    flood_result[
                        "inserted"
                    ],

                "updated":
                    flood_result[
                        "updated"
                    ],
            }
        )

        chirps_rows = (
            load_chirps_weather(
                session,
                datasets[
                    "chirps_daily"
                ],
                location_map,
                sources[
                    "chirps"
                ],
            )
        )

        results.append(
            {
                "component":
                    "chirps_weather",

                "processed":
                    chirps_rows,
            }
        )

        era5_rows = (
            load_era5_weather(
                session,
                datasets[
                    "era5_hourly"
                ],
                location_map,
                sources[
                    "era5_land"
                ],
            )
        )

        results.append(
            {
                "component":
                    "era5_weather",

                "processed":
                    era5_rows,
            }
        )

        environmental_result = (
            load_environmental_features(
                session,
                datasets[
                    "gis_features"
                ],
                location_map,
                sources,
            )
        )

        results.append(
            {
                "component":
                    "environmental_features",

                "processed":
                    environmental_result[
                        "total"
                    ],

                "inserted":
                    environmental_result[
                        "inserted"
                    ],

                "updated":
                    environmental_result[
                        "updated"
                    ],
            }
        )

        session.commit()

    except Exception:

        session.rollback()

        raise

    finally:

        session.close()

    return pd.DataFrame(
        results
    )


#report writer
def save_postgres_load_report(
    report: pd.DataFrame,
) -> None:

    DATABASE_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_file = (
        DATABASE_OUTPUT_DIR
        / "postgres_load_report.csv"
    )

    report.to_csv(
        report_file,
        index=False,
    )

    print(
        f"Saved PostgreSQL load report: "
        f"{report_file}"
    )