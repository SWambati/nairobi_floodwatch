# Nairobi FloodWatch: Near-real-time Flood Monitoring and Risk Prediction Infrastructure for Nairobi
A flood monitoring and flood risk prediction system that combines data engineering, machine learning, geographoc information systems (GIS) and web-based data visualization and presentation to end users (Nairobi Residents, Agencies, System Admins).

## Overview
Nairobi FloodWatch is a near-real-time flood monitoring and flood risk prediction infrastructure developed for Nairobi County, Kenya. The system integrates environmental observations, data processing, machine learning and geographic visualization to provide localized flood-risk assessments.

The infrastructure uses an automated data pipeline to process environmental measurements and a trained Random Forest classifier to generate flood-risk scores. These scores are categorized into LOW, MODERATE and HIGH risk levels for presentation through web dashboards.

The system provides interfaces for residents, authorized agency officers and administrators. It also generates flood advisories and maintains historical monitoring records for subsequent analysis.

Nairobi FloodWatch is an academic research prototype, not an official emergency warning service. Its predictions should not be interpreted as guaranteed flood forecasts or property-level flood assessments.
## Problem Statement
Flood monitoring and early warning in Kenya face challenges associated with fragmented environmental information, limited localized risk assessments and difficulties integrating multiple data sources into centralized monitoring infrastructure.

Nairobi County is particularly vulnerable to flooding because of urbanization, drainage limitations, climate change and environmental degradation.

The project addressed these challenges by developing a centralized data pipeline infrastructure capable of acquiring environmental information, generating flood-risk assessments, storing monitoring records and presenting location-specific results through accessible dashboards.

## System Architecture
External Weather Data Services

             |

             v

      Data Acquisition

             |

             v

   Validation and Processing

             |

             v

     Feature Engineering

             |

             v

 Random Forest Risk Prediction

             |

             v

      Risk Classification

             |

             v

      PostgreSQL Database

             |

       +-----+------+

       |            |

       v            v

  Flood Alerts   FastAPI Backend

                    |

                    v

              Web Dashboards

                    |

        +-----------+-----------+

        |           |           |

        v           v           v

     Resident     Agency      Admin


The operational workflow is scheduled through GitHub Actions, while the web application is deployed on Render and uses PostgreSQL hosted on Neon.

The system also supports historical data preparation and model-development workflows, which are different from the scheduled operational prediction process.
## Core Components
- Data Acquisition: 
The operational acquisition module retrieves weather observations from Open-Meteo API including general weather and ECMWF forecast data. Historical research and model-development workflows incorporated additional environmental and geographic datasets, including CHIRPS rainfall, ERA5-Land, terrain information and flood-related spatial data.

- ETL Pipeline:
The Extract, Transform, and Load (ETL) processes validate environmental observations, address missing or invalid values, derive relevant features and prepare data for prediction and storage.

The operational pipeline processes monitoring locations in batches and includes safeguards against incomplete observations, duplicate records and acquisition failures.

- PostgreSQL Database:
PostgreSQL stores monitoring locations, environmental observations, model metadata, prediction records, flood advisories and relevant authentication and audit information.

SQLAlchemy supports database access, while Alembic manages the versioned database schema.

- Trained Random Forest Classifier
The prediction module uses a trained Random Forest classifier with fifteen environmental and geographic predictors.

The model generates flood-related scores, which are interpreted through separately derived risk thresholds to produce LOW, MODERATE or HIGH classifications.

The system includes model artifact validation and metadata checks to help maintain consistency between the trained model and the operational predictor structure.

- GIS
The GIS components support geographically referenced monitoring and visualization within Nairobi County.

The web application includes Nairobi administrative boundary layers, monitoring locations and geographic information used to contextualize flood-risk assessments.

- Web Dashboards
Nairobi FloodWatch provides three principal dashboard interfaces:

    - Resident Dashboard: Public monitoring information, location search, current flood-risk assessments, historical environmental trends and verified-account CSV exports.

    - Agency Dashboard: Authorized monitoring, geographic analysis, filtered monitoring records and CSV/PDF reporting.

    - Administrator Dashboard: System and flood monitoring, administrative oversight, account-management functions and reporting.

Certain functions require an authenticated account, an assigned role and applicable verification or multi-factor authentication conditions.

- Alert Generation
The alert module creates and maintains advisories based on generated flood risk classifications.

It supports alert issuance, expiration, updates and resolution, while preserving historical alert records for auditing.

Generated advisories represent model-based assessments and do not replace official warnings issued by relevant authorities.

## Technology Stack
- Programming languages: Python 3.11, JavaScript, HTML, CSS
- Data processing: Pandas, Numpy, PyArrow
- Machine learning: Scikit-learn, joblib
- Geospatial processing: GeoPandas, Rasterio, PyOgrio
- Backend: FastAPI, Uvicorn
- Database: PostgreSQL, SQLAlchemy
- Database migrations: Alembic
- Frontend visualization: Leaflet, Chart.js
- Reporting: CSV, jsPDF, jsPDF AutoTable
- Authentication: Google OAuth, Authlib, MFA, local account authentication
- Operational scheduling: GitHub Actions
- Containerization: Docker, Docker Compose
- Web Application hosting:  Render
- Database Hosting:  Neon PostgreSQL
- Version control: Git and GitHub
## Project Structure
nairobi_floodwatch/

|

|-- .github/

|   |-- workflows/         # Operational scheduling

|

|-- config/                 # Application settings and paths

|-- data/                   # Data documentation and spatial resources

|-- docs/                   # Documentation directory

|-- migrations/             # Alembic database migrations

|-- models/                 # Model-related directory

|-- notebooks/              # Experimental notebooks

|

|-- outputs/

|   |-- project_outputs/

|       |-- models/         # Production model artifacts

|       |-- processed/      # Selected operational artifacts

|
|-- scripts/                # Pipeline, preparation and test scripts

|

|-- src/

|   |-- acquisition/        # External data acquisition

|   |-- alerts/             # Alert-related modules

|   |-- auth/               # Authentication and authorization

|   |-- dashboard/          # Monitoring queries and API routes

|   |-- database/           # Database models and access

|   |-- etl/                # Validation and preprocessing

|   |-- features/           # Feature engineering

|   |-- gis/                # Geographic processing

|   |-- model/              # Model inference and risk classification

|   |-- operational/        # Operational prediction workflow

|   |-- web/

|       |-- static/         # CSS, JavaScript, GIS and assets

|       |-- templates/      # HTML interfaces

|

|-- tests/                  # Automated unit tests

|

|-- .env.example            # Example environment configuration

|-- .gitignore

|-- alembic.ini

|-- app.py                  # FastAPI application entry point

|-- docker-compose.yml

|-- Dockerfile

|-- requirements.txt

|-- README.md

Some research datasets and generated files are stored outside Git version control because of their size, provenance or access requirements. Their absence from a fresh clone should not be interpreted as an empty production database.

## Installation
Prerequisites

Local development requires Python 3.11, Git, PostgreSQL, and the dependencies specified in requirements.txt.

Docker Desktop may be used to run the local PostgreSQL service.

1. Clone the repository

git clone https://github.com/SWambati/nairobi_floodwatch.git
cd nairobi_floodwatch

2. Create a virtual environment

python -m venv .venv
.\.venv\Scripts\Activate.ps1

3. Install dependencies

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

For the current local Alembic migration configuration and unit-testing commands, install the additional development dependencies:

python -m pip install "psycopg[binary]" pytest

4. Configure environment variables

Create a local environment configuration from the provided template:

Copy-Item .env.example .env

Update .env with the appropriate local settings.

The following values illustrate a Docker-based development configuration:

DATABASE_HOST=127.0.0.1

DATABASE_PORT=5433

DATABASE_NAME=flood_monitoring

DATABASE_USER=flood_app

DATABASE_PASSWORD=REPLACE_WITH_LOCAL_PASSWORD

DATABASE_URL=postgresql+psycopg2://flood_app:REPLACE_WITH_LOCAL_PASSWORD@127.0.0.1:5433/flood_monitoring

APP_NAME=Nairobi FloodWatch

APP_ENV=development

APP_TIMEZONE=Africa/Nairobi

LOG_LEVEL=INFO

FLOODWATCH_ENV=development

FLOODWATCH_BASE_URL=http://127.0.0.1:8010

FLOODWATCH_OAUTH_SESSION_SECRET=REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS


The application requires FLOODWATCH_OAUTH_SESSION_SECRET at startup.

Generate a suitable development secret using:

python -c "import secrets; print(secrets.token_urlsafe(48))"

Keep the individual DATABASE_* settings consistent with DATABASE_URL, including the port and database credentials. The current Alembic configuration derives its connection settings from the individual database variables.

Additional services, such as Google OAuth and transactional email delivery, require provider-specific credentials configured through environment variables. Supported email configuration includes Brevo's transactional email API and development SMTP settings.

Passwords, secrets, API Keys, OAuth clients, .env variables and other confidential configurations should not be committed to GitHub.

5. Start PostgreSQL

Using Docker Compose: docker compose up -d postgres

Wait until the database container is healthy.

6. Apply database migrations

For a newly provisioned local database: python -m alembic upgrade head

The application also initializes required authentication-related database structures during startup.

Important: Database migrations create the required schema but do not automatically populate the complete historical datasets, static features or operational monitoring records. Reproducing the full prediction environment requires the relevant authorized datasets and data-loading procedures.
## Running the System

1. Start the FastAPI web application

From the project root, with the environment configured: python -m uvicorn app:app --host 127.0.0.1 --port 8010

The application can then be accessed through: Homepage: http://127.0.0.1:8010/

Resident Dashboard: http://127.0.0.1:8010/resident

API Documentation: http://127.0.0.1:8010/api/docs

Agency and administrator interfaces require appropriately authorized accounts.

The dashboards depend on monitoring records in PostgreSQL. On a newly created, unpopulated database, the complete monitoring experience will not be available until the required data has been prepared and loaded.

2. Operational prediction workflow

The operational pipeline entry point is: python -m scripts.run_operational_prediction

It performs environmental data acquisition, feature generation, model inference, risk classification and operational persistence.

This command makes external API requests and writes operational records to the database specified by DATABASE_URL. It should only be executed against an authorized, correctly configured environment with the required monitoring locations, static features and model artifacts.

Avoid unnecessary repeated execution because external weather services may enforce rate limits.

3. Hosted deployment

The publicly accessible application is deployed at: https://nairobi-floodwatch.onrender.com

The production FastAPI service runs on Render, uses Neon PostgreSQL for persistent storage, and relies on GitHub Actions for scheduled operational processing.

The scheduled workflow is defined in: .github/workflows/floodwatch-operational.yml

It is configured for two daily execution windows at 03:07 and 15:07 UTC (06:07 and 18:07 East Africa Time). Actual execution may be delayed, so these times should not be interpreted as guaranteed data-update times.

## Development Workflow
Development was managed using Git and GitHub with feature-specific branches and pull requests.

The repository uses main as its principal integration branch. An active GitHub ruleset requires pull requests before changes are merged into main and protects the branch against deletion and non-fast-forward updates.

Development branches were organized around implementation tasks such as:

- Data acquisition and ETL validation

- PostgreSQL database development

- Feature engineering and machine learning

- GIS integration

- Authentication, dashboards, and alerts

- Deployment and system testing

- Documentation

GitHub Issues and milestones were used to record planned work, track progress and organize development activities. Pull requests provided an auditable record of changes made during implementation, debugging, testing and deployment.

Examples of repository history and project management evidence are available through:

- Branches

- Pull Requests

- Issues

- GitHub Actions

- Commit History

## Testing

Testing covered functional behavior, unit-level validation, system integration, data integrity, authorization, exception handling, operational reliability, reporting, performance and usability.

Automated tests are primarily located within the following directories:

- tests/ — unit tests and controlled validation fixtures

- scripts/ — integration, operational, database, dashboard and system-testing utilities

# Unit testing

The core unit test suite can be executed using: python -m pytest -q tests/test_unit_core.py

The completed evaluation recorded 54 passing unit tests after identified validation defects were corrected.

# Selected production smoke tests

The repository includes read-only tests for selected deployed monitoring endpoints and report-generation behavior.

For example:

python -m scripts.test_tc012_production_smoke

python -m scripts.test_tc014_exports

These scripts use the deployed FloodWatch URL by default and make HTTP requests to the public application. Review their configuration and scope before execution.

To direct supported tests toward a locally running instance in PowerShell:

$env:FLOODWATCH_BASE_URL="http://127.0.0.1:8010"

Some integration tests require an initialized database, external data availability, or specific authentication conditions. Other scripts deliberately simulate failures or exercise database transactions and should only be run in the intended test environment.

The academic system evaluation documented sixteen test-case groups, including their scenarios, expected behavior, observed outcomes, defects and retesting results.

## Project Status

Status: deployed and evaluated.

The implemented infrastructure includes operational data acquisition, Random Forest inference, PostgreSQL persistence, GIS-supported dashboards, user authentication, report generation, flood advisories and automated operational scheduling.

System testing demonstrated successful operation across the main implemented functions. However, limitations remain concerning external weather API rate limiting and the punctuality of scheduled workflow execution.

The project is intended for research, demonstration and academic evaluation. It has not been established as an operational public emergency-warning system.

## Authors / Academic Context

Repository Owner: SWambati

Project: Final-year Informatics and Computer Science research project

Institution: Strathmore University, Kenya

Study Area: Nairobi County, Kenya

Research Areas: Data Engineering, Machine Learning, Geographic Information Systems, Environmental Monitoring, and Information Systems

## Licensing and Access

The source repository is publicly accessible for inspection and academic evaluation.

No standalone open-source license was identified in the repository at the time this README was drafted. Public visibility does not, by itself, grant permission to redistribute or reuse the code. Any future licensing or redistribution terms should be confirmed by the repository owner.

Third-party datasets, libraries, and external services remain subject to their respective licenses and terms of use.