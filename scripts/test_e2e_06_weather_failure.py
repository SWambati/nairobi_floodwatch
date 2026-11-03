import json
import sys
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from requests.exceptions import Timeout

import scripts.run_operational_prediction as pipeline

OUTPUT_DIR = (Path("outputs") / "project_outputs" / "testing")

# These functions must never run when weather acquisition has failed.
DOWNSTREAM_FUNCTIONS = (
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
    #In-memory replacement for OperationalMonitor.

    def __init__(self):
        self.entered_stages = []
        self.failed_stages = []
        self.metrics = {}
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
        self.metrics[name] = value

def check(condition, message):
    if not condition:
        raise AssertionError(message)

    print(f"[PASS] {message}", flush=True)

def run_scenario(name, response, expected_type):
    print(f"\n--- {name} ---")
    monitor = RecordingMonitor()
    with ExitStack() as stack:

        # Prevent real monitoring/database access.
        stack.enter_context(patch.object(pipeline, "OperationalMonitor", return_value=monitor))
        stack.enter_context(patch.object(pipeline, "create_database_engine", return_value=object()))

        # A synthetic set of 237 monitoring locations.
        # The pipeline only needs len(locations) before weather acquisition.
        stack.enter_context(patch.object(pipeline, "load_operational_locations", return_value=list(range(237))))
        client_class = stack.enter_context(patch.object(pipeline, "OpenMeteoClient"))

        fetch = (client_class.return_value.fetch_locations_batched)

        if isinstance(response, Exception):
            fetch.side_effect = response
        else:
            fetch.return_value = response

        # Guard every downstream stage.
        downstream = {}

        for function_name in DOWNSTREAM_FUNCTIONS:
            mock_function = stack.enter_context(patch.object(pipeline, function_name))
            downstream[function_name] = mock_function

        captured_error = None

        try:
            pipeline.main()
        except Exception as exc:
            captured_error = exc

        check(captured_error is not None, "Pipeline rejected failed acquisition")
        check(isinstance(captured_error, expected_type), "Expected failure type propagated")
        check(fetch.call_count == 1, "Weather acquisition boundary invoked once")
        check(("open_meteo_acquisition" in monitor.failed_stages), "Acquisition stage marked failed")
        check(monitor.pipeline_failed, "Pipeline failure propagated to monitor")

        called = [function_name for function_name, mock_function in downstream.items() if mock_function.called]

        check(not called, "No downstream processing or persistence")

        # Confirm no later pipeline stages started.
        check(monitor.entered_stages == ["database_connection", "load_locations", "open_meteo_acquisition"], "Pipeline stopped at acquisition")

        return {
            "scenario": name,
            "result": "PASS",
            "error_type": type(captured_error).__name__,
            "failed_stage": "open_meteo_acquisition",
            "downstream_calls": len(called),
            "database_writes": 0,
            "live_api_requests": 0,
        }


def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-06C")
    print("=" * 50)
    print("Mode: CONTROLLED FAILURE INJECTION")
    print("External requests: DISABLED")
    print("Database writes: DISABLED")

    scenarios = [
        ("HTTP 429 — RATE LIMIT", RuntimeError("Open-Meteo request failed after 4 attempts: 429 Too Many Requests"), RuntimeError),
        ("NETWORK TIMEOUT", Timeout("Synthetic Open-Meteo connection timeout"), Timeout),
        ("MISSING REQUIRED WEATHER DATASET", {"generic_hourly": [], "current": []}, KeyError)
    ]

    results = []

    for name, response, expected_type in scenarios:
        results.append(run_scenario(name, response, expected_type))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d_%H%M%S")
    evidence_path = (OUTPUT_DIR / f"e2e_06c_weather_failure_{timestamp}.json")
    evidence = {
        "test_id": "E2E-06C",
        "executed_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
        "mode": "isolated_failure_injection",
        "scenarios": results,
        "passed": len(results),
        "total": len(scenarios),
        "note": ("Failures verified using an in-memory monitor. Real monitoring database persistence was not exercised."),
    }

    with evidence_path.open("w", encoding="utf-8") as file:
        json.dump(evidence, file, indent=2)

    print("\n" + "=" * 50)
    print(f"Scenarios passed: " f"{len(results)}/{len(scenarios)}")
    print("E2E-06C RESULT: PASS")
    print("=" * 50)

    print("No operational records modified.")
    print("No Open-Meteo requests sent.")
    print("Evidence:", evidence_path)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-06C RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
