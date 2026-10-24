#Read-only PostgreSQL queries for FloodWatch admin

from uuid import UUID
from sqlalchemy import text

def get_operational_overview(engine):
    # Summarize recent pipeline executions and their results.

    with engine.connect() as connection:
        latest = connection.execute(text("""
            SELECT run_id, job_name, status, started_at,
                   finished_at, duration_ms, metrics, error_type
            FROM operational_job_runs
            ORDER BY started_at DESC
            LIMIT 1
        """)).mappings().first()

        last_success = connection.execute(text("""
            SELECT run_id, finished_at, duration_ms
            FROM operational_job_runs
            WHERE status = 'succeeded'
            ORDER BY finished_at DESC
            LIMIT 1
        """)).mappings().first()

        last_failure = connection.execute(text("""
            SELECT run_id, finished_at, error_type
            FROM operational_job_runs
            WHERE status = 'failed'
            ORDER BY finished_at DESC
            LIMIT 1
        """)).mappings().first()

        counts = connection.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'succeeded') AS succeeded,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                COUNT(*) FILTER (WHERE status = 'running') AS running
            FROM operational_job_runs
            WHERE started_at >= NOW() - INTERVAL '24 hours'
        """)).mappings().one()

    return {
        "latest_run": dict(latest) if latest else None,
        "last_success": dict(last_success) if last_success else None,
        "last_failure": dict(last_failure) if last_failure else None,
        "runs_last_24h": dict(counts),
    }


def get_recent_runs(engine, limit=20):
    # Return recent executions, including their recorded metrics.

    limit = max(1, min(int(limit), 100))

    query = text("""
        SELECT run_id, job_name, status, started_at,
               finished_at, duration_ms, metrics, error_type
        FROM operational_job_runs
        ORDER BY started_at DESC
        LIMIT :limit
    """)

    with engine.connect() as connection:
        rows = connection.execute(query, {"limit": limit}).mappings()
        return [dict(row) for row in rows]


def get_run_events(engine, run_id, limit=200):
    # Retrieve logs for one execution, ordered chronologically.

    run_id = str(UUID(str(run_id)))
    limit = max(1, min(int(limit), 500))

    query = text("""
        SELECT event_id, run_id, occurred_at, level,
               component, message, duration_ms, details
        FROM operational_events
        WHERE run_id = CAST(:run_id AS UUID)
        ORDER BY occurred_at ASC, event_id ASC
        LIMIT :limit
    """)

    with engine.connect() as connection:
        rows = connection.execute(
            query, {"run_id": run_id, "limit": limit}
        ).mappings()

        return [dict(row) for row in rows]


def get_latest_stage_results(engine):
    # Get the latest recorded state of each stage in the newest run.

    query = text("""
        WITH latest_run AS (
            SELECT run_id
            FROM operational_job_runs
            ORDER BY started_at DESC
            LIMIT 1
        )
        SELECT DISTINCT ON (e.component)
               e.component,
               e.message,
               e.occurred_at,
               e.duration_ms,
               CASE
                   WHEN e.message = 'Stage completed' THEN 'succeeded'
                   WHEN e.message = 'Stage failed' THEN 'failed'
                   ELSE 'running'
               END AS status
        FROM operational_events e
        JOIN latest_run r ON e.run_id = r.run_id
        WHERE e.message IN (
            'Stage started', 'Stage completed', 'Stage failed'
        )
        ORDER BY e.component, e.occurred_at DESC
    """)

    with engine.connect() as connection:
        rows = connection.execute(query).mappings()
        return [dict(row) for row in rows]


def get_recent_errors(engine, limit=30):
    # Read recent logged errors across operational runs.

    limit = max(1, min(int(limit), 100))

    query = text("""
        SELECT event_id, run_id, occurred_at,
               component, message, details
        FROM operational_events
        WHERE level = 'ERROR'
        ORDER BY occurred_at DESC
        LIMIT :limit
    """)

    with engine.connect() as connection:
        rows = connection.execute(query, {"limit": limit}).mappings()
        return [dict(row) for row in rows]