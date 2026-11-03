import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.operational.run_lock import operational_run_lock
import scripts.run_operational_prediction as pipeline

OUTPUT_DIR = Path("outputs") / "project_outputs" / "testing"

def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}", flush=True)

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-06E")
    print("=" * 50)
    print("Mode: POSTGRESQL SCHEDULER LOCK TEST")

    results = []

    with operational_run_lock() as first:
        check(first, "First execution acquired the database lock")

        with operational_run_lock() as second:
            check(not second, "Concurrent execution denied")

        with patch.object(pipeline, "main") as mocked_pipeline:
            result = pipeline.run_scheduled()
            check(result is False, "Scheduled execution skipped while locked")
            check(not mocked_pipeline.called, "Skipped execution did not start the pipeline")

    results.append("Concurrent execution prevention: PASS")

    with patch.object(pipeline, "main") as mocked_pipeline:
        result = pipeline.run_scheduled()
        check(result is True, "Scheduled execution acquired released lock")
        check(mocked_pipeline.call_count == 1, "Pipeline executed exactly once")

    results.append("Lock reuse after completion: PASS")

    with patch.object(pipeline, "main", side_effect=RuntimeError("Synthetic pipeline failure")):
        try:
            pipeline.run_scheduled()
        except RuntimeError as exc:
            check(str(exc) == "Synthetic pipeline failure", "Pipeline exception propagated")
        else:
            raise AssertionError("Pipeline exception was unexpectedly suppressed")

    with operational_run_lock() as acquired:
        check(acquired, "Lock released after pipeline failure")

    results.append("Lock release after exception: PASS")

    with patch("src.operational.run_lock.create_database_engine", side_effect=ConnectionError("Synthetic database outage")):
        with patch.object(pipeline, "main") as mocked_pipeline:
            try:
                pipeline.run_scheduled()
            except ConnectionError:
                check(True, "Database outage prevented scheduled execution")
            else:
                raise AssertionError("Database outage did not stop execution")

            check(not mocked_pipeline.called, "Pipeline did not bypass unavailable lock")

    results.append("Database outage fail-closed: PASS")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d_%H%M%S")
    evidence_path = OUTPUT_DIR / f"e2e_06e_scheduler_lock_{timestamp}.json"

    evidence = {
        "test_id": "E2E-06E",
        "executed_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
        "results": results,
        "passed": len(results),
        "total": 4,
        "note": "Real PostgreSQL advisory locks tested; operational pipeline execution mocked.",
    }

    with evidence_path.open("w", encoding="utf-8") as file:
        json.dump(evidence, file, indent=2)

    print("\n" + "=" * 50)
    print(f"Scenarios passed: {len(results)}/4")
    print("E2E-06E RESULT: PASS")
    print("=" * 50)
    print("No predictions, alerts or weather records modified.")
    print("Evidence:", evidence_path)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-06E RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
