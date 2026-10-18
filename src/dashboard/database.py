from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from config.settings import get_settings

#share 1 SQLAlchemy connection pool across all web requests
@lru_cache(maxsize=1)
def get_database_engine() -> Engine:
    settings = get_settings()

    return create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800, connect_args={"connect_timeout": 10})

def fetch_rows(query: str, parameters: dict | None = None) -> list[dict]:
    #execute application-defined, read-only SQL queries
    #bind query values separately rather than formatting user input into SQL
    
    engine = get_database_engine()

    with engine.connect() as connection:
        result = connection.execute(text(query), parameters or {})

        return [dict(row) for row in result.mappings().all()]

def check_database_connection() -> bool:
    #fail explicitly when PostgreSQL is unavailable
    engine = get_database_engine()

    with engine.connect() as connection:
        return connection.execute(text("SELECT 1")).scalar_one() == 1