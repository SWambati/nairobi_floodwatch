import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


#locate project root regardless of the current working directiory
PROJECT_ROOT = Path(__file__).resolve().parents[1]


#load local configuration without overriding existing .env
#histed deployments can supply their own environment variables
load_dotenv(PROJECT_ROOT / ".env", override=False)

@dataclass(frozen=True)
class AppSettings:
    app_name: str
    environment: str
    database_url: str
    timezone: str
    log_level: str

def get_settings() -> AppSettings:
    #The operational data pipeline, model and Streamlit application will share the same database configuration

    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise EnvironmentError("DATABASE_URL is missing. COnfigure it in your .env file or set it as an environment variable")

    #differnciate between developmen, testing and deployed environments
    environment = os.getenv("APP_ENV", "development").strip().lower()

    if environment not in {"development", "testing", "production"}:
        raise ValueError(f"Unsupported application environment: {environment}")

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Unsupported log level: {log_level}")

    return AppSettings(
        app_name=os.getenv("APP_NAME", "Nairobi FloodWatch"),
        environment=environment,
        database_url=database_url,
        timezone=os.getenv("APP_TIMEZONE", "Africa/Nairobi"),
        log_level=log_level
    )

#database
DATABASE_HOST = os.getenv("DATABASE_HOST", "localhost")
DATABASE_PORT = int(os.getenv("DATABASE_PORT", 5432))
DATABASE_NAME = os.getenv("DATABASE_NAME", "flood_monitoring")
DATABASE_USER = os.getenv("DATABASE_USER", "postgres")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "")

DATABASE_URL = (f"postgresql+psycopg://"
                f"{DATABASE_USER}:"
                f"{DATABASE_PASSWORD}@"
                f"{DATABASE_HOST}:{DATABASE_PORT}/"
                f"{DATABASE_NAME}"
                )

#APIs

OPEN_METEO_BASE_URL = os.getenv("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast")

#project settings

PROJECT_NAME = "Nairobi FloodWatch"
MODEL_NAME = "random_forest"
DEFAULT_TIMEZONE = "Africa/Nairobi"