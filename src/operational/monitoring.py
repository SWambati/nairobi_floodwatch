#Operational monitoring for the Nairobi FloodWatch pipeline.

import json
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger("floodwatch.monitoring")

DEFAULT_LOG = (Path(__file__).resolve().parents[2] / "logs" / "operational_runs.jsonl")

RUN_TABLE = """
CREATE TABLE IF NOT EXISTS operational_job_runs (
    run_id UUID PRIMARY KEY,
    job_name TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'succeeded', 'failed')),
    duration_ms BIGINT,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_type TEXT
)
"""

EVENT_TABLE = """
CREATE TABLE IF NOT EXISTS operational_events (
    event_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES operational_job_runs(run_id),
    occurred_at TIMESTAMPTZ NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    duration_ms BIGINT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
)
"""

UPSERT_RUN = """
INSERT INTO operational_job_runs
    (run_id, job_name, started_at, finished_at,
     status, duration_ms, metrics, error_type)
VALUES
    (CAST(:run_id AS UUID), :job_name,
     CAST(:started_at AS TIMESTAMPTZ),
     CAST(:finished_at AS TIMESTAMPTZ),
     :status, :duration_ms,
     CAST(:metrics AS JSONB), :error_type)
ON CONFLICT (run_id) DO UPDATE SET
    finished_at = EXCLUDED.finished_at,
    status = EXCLUDED.status,
    duration_ms = EXCLUDED.duration_ms,
    metrics = EXCLUDED.metrics,
    error_type = EXCLUDED.error_type
"""

INSERT_EVENT = """
INSERT INTO operational_events
    (event_id, run_id, occurred_at, level,
     component, message, duration_ms, details)
VALUES
    (CAST(:event_id AS UUID), CAST(:run_id AS UUID),
     CAST(:occurred_at AS TIMESTAMPTZ),
     :level, :component, :message,
     :duration_ms, CAST(:details AS JSONB))
ON CONFLICT (event_id) DO NOTHING
"""


class OperationalMonitor:
    # Track a pipeline run using local logs and PostgreSQL.

    def __init__(self, job_name="operational_prediction", log_path=None):
        self.job_name = job_name
        self.run_id = str(uuid4())
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG
        self.started_at = None
        self.finished_at = None
        self.duration_ms = None
        self.status = "running"
        self.error_type = None
        self.metrics = {}
        self._start_clock = None
        self._engine = None
        self._pending = []

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc).isoformat()

    def __enter__(self):
        self.started_at = self._utc_now()
        self._start_clock = time.perf_counter()
        self._event("INFO", "pipeline", "Run started")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.finished_at = self._utc_now()
        self.duration_ms = round((time.perf_counter() - self._start_clock) * 1000)
        self.status = "failed" if exc_type else "succeeded"
        self.error_type = exc_type.__name__ if exc_type else None

        self._event(
            "ERROR" if exc_type else "INFO",
            "pipeline",
            f"Run {self.status}",
            duration_ms=self.duration_ms,
            details=({"error_type": self.error_type} if exc_type else {}),
        )

        return False

    @contextmanager
    def stage(self, name):
        """Measure a processing stage without suppressing its errors."""
        start = time.perf_counter()
        self._event("INFO", name, "Stage started")

        try:
            yield self

        except Exception as exc:
            self._event(
                "ERROR",
                name,
                "Stage failed",
                duration_ms=round((time.perf_counter() - start) * 1000),
                details={"error_type": type(exc).__name__},
            )
            raise

        else:
            self._event(
                "INFO",
                name,
                "Stage completed",
                duration_ms=round((time.perf_counter() - start) * 1000),
            )

    def metric(self, name, value):
        """Store numerical metrics without exposing sensitive data."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError("Metric name must be a lowercase identifier")

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Metrics must be numeric")

        if not (-float("inf") < value < float("inf")):
            raise ValueError("Metric value must be finite")

        self.metrics[name] = value

        self._event(
            "INFO",
            "metrics",
            f"Updated {name}",
            details={name: value},
        )

    def attach_engine(self, engine):
        """Initialize monitoring tables when PostgreSQL is available."""
        from sqlalchemy import text

        try:
            with engine.begin() as connection:
                connection.execute(text(RUN_TABLE))
                connection.execute(text(EVENT_TABLE))

                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_operational_runs_started "
                    "ON operational_job_runs (started_at DESC)"
                ))

                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_operational_events_time "
                    "ON operational_events (occurred_at DESC)"
                ))

            self._engine = engine
            self._flush()

            return self._engine is not None

        except Exception as exc:
            self._engine = None
            logger.warning(
                "Monitoring database unavailable (%s)",
                type(exc).__name__,
            )
            return False

    def _event(
        self,
        level,
        component,
        message,
        duration_ms=None,
        details=None,
    ):
        event = {
            "event_id": str(uuid4()),
            "run_id": self.run_id,
            "occurred_at": self._utc_now(),
            "job_name": self.job_name,
            "level": level,
            "component": component,
            "message": message,
            "duration_ms": duration_ms,
            "details": details or {},
        }

        self._pending.append(event)

        # Always attempt a local record, including during DB outages.
        try:
            self.log_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

        except OSError as exc:
            logger.warning(
                "Monitoring file write failed (%s)",
                type(exc).__name__,
            )

        self._flush()

    def _flush(self):
        if (
            self._engine is None
            or not self._pending
            or self.started_at is None
        ):
            return

        from sqlalchemy import text

        try:
            with self._engine.begin() as connection:
                connection.execute(text(UPSERT_RUN), {
                    "run_id": self.run_id,
                    "job_name": self.job_name,
                    "started_at": self.started_at,
                    "finished_at": self.finished_at,
                    "status": self.status,
                    "duration_ms": self.duration_ms,
                    "metrics": json.dumps(self.metrics),
                    "error_type": self.error_type,
                })

                for event in self._pending:
                    connection.execute(text(INSERT_EVENT), {
                        "event_id": event["event_id"],
                        "run_id": self.run_id,
                        "occurred_at": event["occurred_at"],
                        "level": event["level"],
                        "component": event["component"],
                        "message": event["message"],
                        "duration_ms": event["duration_ms"],
                        "details": json.dumps(event["details"]),
                    })

            self._pending.clear()

        except Exception as exc:
            # Monitoring failures must not interrupt predictions.
            self._engine = None

            logger.warning(
                "Monitoring PostgreSQL write failed (%s)",
                type(exc).__name__,
            )