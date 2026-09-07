from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#data
DATA_ROOT = Path(os.getenv("DATA_ROOT", PROJECT_ROOT /"data"/"project_data"))

FLOOD_LABELS_DIR = DATA_ROOT/ "01_flood_labels"
WEATHER_DIR = DATA_ROOT/ "02_weather"
SPATIAL_DIR = DATA_ROOT/ "03_spatial"
ENVIRONMENTAL_DIR = DATA_ROOT/ "04_environmental"
OPERATIONAL_DIR = DATA_ROOT/ "05_operational"
DATA_DOCUMENTATION_DIR = DATA_ROOT/ "06_documentation"

#flood labels
GROUNDSOURCE_DIR = FLOOD_LABELS_DIR/ "groundsource"
MASTER_CATALOGUE_DIR = FLOOD_LABELS_DIR/ "master_catalogue"

#weather
CHIRPS_DIR = WEATHER_DIR/ "chirps_v3"
ERA5_LAND_DIR = WEATHER_DIR/ "era5_land"

#spatial
BOUNDARIES_DIR = SPATIAL_DIR/ "nairobi_boundaries"

#environmental
ICPAC_DIR = ENVIRONMENTAL_DIR/ "icpac_flood_hazard"
GHSL_DIR = ENVIRONMENTAL_DIR / "ghsl_built_surface"
WORLDCOVER_DIR = ENVIRONMENTAL_DIR / "esa_worldcover"
NASADEM_DIR = ENVIRONMENTAL_DIR / "nasadem"

#generated outputs
OUTPUT_ROOT = Path(os.getenv("OUTPUT_ROOT", PROJECT_ROOT / "outputs"/ "project_outputs"))

PROCESSED_DIR = OUTPUT_ROOT/ "processed"
FEATURES_DIR = OUTPUT_ROOT/ "features"
MODELS_DIR = OUTPUT_ROOT/ "models"
REPORTS_DIR = OUTPUT_ROOT/ "reports"
LOGS_DIR = OUTPUT_ROOT/ "logs"