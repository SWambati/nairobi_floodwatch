from contextlib import contextmanager
from sqlalchemy import text

from src.operational.persistence import create_database_engine

LOCK_NAMESPACE = 52001
LOCK_ID = 6018
LOCK_PARAMS = {"namespace": LOCK_NAMESPACE, "job": LOCK_ID}

@contextmanager
def operational_run_lock():
    engine = create_database_engine()

    try:
        with engine.connect() as conn:
            acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:namespace, :job)"), LOCK_PARAMS).scalar_one())
            conn.commit()

            try:
                yield acquired
            finally:
                if acquired:
                    released = conn.execute(text("SELECT pg_advisory_unlock(:namespace, :job)"), LOCK_PARAMS).scalar_one()
                    conn.commit()

                    if not released:
                        raise RuntimeError("Operational pipeline lock could not be released.")
    finally:
        engine.dispose()
