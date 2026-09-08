from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool


# ============================================================
# PROJECT IMPORT PATH
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# PROJECT CONFIGURATION
# ============================================================

from config.settings import DATABASE_URL
from src.database.base import Base

# Importing models registers all tables with Base.metadata.
import src.database.models  # noqa: F401, E402


# ============================================================
# ALEMBIC CONFIG
# ============================================================

config = context.config


# Set database URL from the project's environment configuration.
#
# Percent characters must be escaped because Alembic uses
# ConfigParser internally.
config.set_main_option(
    "sqlalchemy.url",
    DATABASE_URL.replace("%", "%%"),
)


if config.config_file_name is not None:
    fileConfig(config.config_file_name)


target_metadata = Base.metadata


# ============================================================
# OFFLINE MIGRATIONS
# ============================================================

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ============================================================
# ONLINE MIGRATIONS
# ============================================================

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(
            config.config_ini_section,
            {}
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# ============================================================
# EXECUTION
# ============================================================

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()