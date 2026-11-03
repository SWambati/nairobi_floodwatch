import json
import sys
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import scripts.run_operational_prediction as pipeline

OUTPUT_DIR = Path("outputs") / "project_outputs" / "testing"

DOWNSTREAM_FUNCTIONS = (
    "OpenMeteoClient",
    "save_open_meteo_snapshot",
    "build_weather_features",
    "load_static_features",
    "assemble_model_features",
    "FloodInferenceEngine",
    "FloodRiskClassifier",
    "ensure_data_source",
    "ensure_production_model",
    "persist_current_weather",
    "persist_predictions",
    "persist_alerts",
)

class RecordingMonitor:
    def __init__(self):
        self.entered_stages = []
        self.failed_stages = []
        self.pipeline_failed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.pipeline_failed = exc_type is not None
        return False

    @contextmanager
    def stage(self, name):
        self.entered_stages.append(name)
        try:
            yield
        except Exception:
            self.failed_stages.append(name)
            raise

    def attach_engine(self, engine):
        return True

    def metric(self, name, value):
        pass

def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}", flush=True)

def run_scenario(name, failure_stage):
    print(f"\n--- {name} ---")
    monitor = RecordingMonitor()

    with ExitStack() as stack:
        stack.enter_context(patch.object(pipeline, "OperationalMonitor", return_value=monitor))

        engine = stack.enter_context(patch.object(pipeline, "create_database_engine", return_value=object()))
        locations = stack.enter_context(patch.object(pipeline, "load_operational_locations", return_value=[]))

        if failure_stage == "database_connection":
            engine.side_effect = ConnectionError("Synthetic database connection failure")
        else:
            locations.side_effect = ConnectionError("Synthetic database query failure")

        downstream = {
            name: stack.enter_context(patch.object(pipeline, name))
            for name in DOWNSTREAM_FUNCTIONS
        }

        error = None
        try:
            pipeline.main()
        except Exception as exc:
            error = exc

        expected_stages = (
            ["database_connection"] if failure_stage == "database_connection"
            else ["database_connection", "load_locations"]
        )

        check(isinstance(error, ConnectionError), "Database failure propagated")
        check(monitor.failed_stages == [failure_stage], "Correct pipeline stage marked failed")
        check(monitor.entered_stages == expected_stages, "Pipeline terminated at failed stage")
        check(monitor.pipeline_failed, "Failure propagated to monitoring interface")
        check(not any(mock.called for mock in downstream.values()), "No weather acquisition, inference or persistence executed")

        if failure_stage == "database_connection":
            check(not locations.called, "Location query skipped after connection failure")
        else:
            check(locations.call_count == 1, "Failed location query invoked once")

        return {
            "scenario": name,
            "result": "PASS",
            "failed_stage": failure_stage,
            "error_type": type(error).__name__,
            "downstream_calls": sum(mock.call_count for mock in downstream.values()),
        }

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-06D")
    print("=" * 50)
    print("Mode: ISOLATED DATABASE FAILURE INJECTION")
    print("Real database access: DISABLED")

    scenarios = [
        ("DATABASE CONNECTION FAILURE", "database_connection"),
        ("MONITORING LOCATION QUERY FAILURE", "load_locations"),
    ]

    results = [run_scenario(name, stage) for name, stage in scenarios]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d_%H%M%S")
    evidence_path = OUTPUT_DIR / f"e2e_06d_database_failure_{timestamp}.json"

    evidence = {
        "test_id": "E2E-06D",
        "executed_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
        "mode": "isolated_failure_injection",
        "scenarios": results,
        "passed": len(results),
        "total": len(scenarios),
        "note": "Database operations and monitoring were mocked; real PostgreSQL recovery was not exercised.",
    }

    with evidence_path.open("w", encoding="utf-8") as file:
        json.dump(evidence, file, indent=2)

    print("\n" + "=" * 50)
    print(f"Scenarios passed: {len(results)}/{len(scenarios)}")
    print("E2E-06D RESULT: PASS")
    print("=" * 50)
    print("No operational records modified.")
    print("Evidence:", evidence_path)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-06D RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
