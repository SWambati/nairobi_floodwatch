import os
from dotenv import load_dotenv

load_dotenv()

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