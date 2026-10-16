from functools import lru_cache
import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from config.settings import get_settings

#tables already populated by historical and operational pipelines
REQUIRED_TABLES = ("Location", "weather_data", "flood_risk_prediction", "flood_alert", "model", "data_source", "environment_feature")

#PostgreSQL connection
@lru_cache(maxsize=1)
def get_database_engine() -> Engine:
    #Reuse one SQLAlchemy connection pool across all dashboard requests.
    #pool_pre_ping checks connection before using them
    #pool_recycle replaces older pooled connections when needed

    settings = get_settings()

    return create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800, connect_args={"connect_timeout": 10})

#database health check
def check_database_connection() -> dict:
    engine = get_database_engine()

    with engine.connect() as connection:
        result = connection.execute(
            text(
                """
                SELECT
                    current_database() AS database_name,
                    current_user AS database_user,
                    NOW() as server_time
                """)
            ).mappings().one()

    return dict(result)

#schema validation
def get_missing_tables() -> list[str]:
    #confirm that the database has tables required by the dashboard. This prevents displaying fake/ placeholer records when a table has not been created or the wrong database is connected

    engine = get_database_engine()
    existing = set(inspect(engine).get_table_names(schema="public"))

    return sorted(set(REQUIRED_TABLES) - existing)

#reusable dashboard queries
def fetch_dataframe(query: str, parameters: dict | None = None) -> pd.DataFrame:
    #read application-defied SQL queries into pandas DataFrames
    #query parameters are bound separately to prevent SQL injection

    engine = get_database_engine()

    with engine.connect() as connection:
        return pd.read_sql_query(text(query), connection, params=parameters or {})

#database record counts
def get_table_counts() -> dict[str, int]:
    missing = get_missing_tables()

    if missing:
        raise RuntimeError(f"Required database tables are missing: {missing}")
    
    counts = {}
    engine = get_database_engine()

    with engine.connect() as connection:
        for table_name in REQUIRED_TABLES:
            #table name come only from the fixed internal allowlist above.
            #user-supplied table names must never be inserted into SQL

            count = connection.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()

            counts[table_name] = int(count)
            
    return counts
