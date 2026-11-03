import json
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPETITIONS = 3
EXPECTED_LOCATIONS = 237
EXPECTED_HOURLY_ROWS = 45504
OUTPUT_DIR = (Path("outputs") / "project_outputs" / "testing")
STAGES = (
    "Location loading",
    "Weather acquisition",
    "Weather features",
    "Static features",
    "Feature assembly",
    "Model inference",
    "Risk classification",
)

def extract_number(output, label):
    match = re.search(rf"^{re.escape(label)}:\s*(\d+)\s*$", output, re.MULTILINE)
    return int(match.group(1)) if match else None

def extract_duration(output, label):
    match = re.search(rf"^{re.escape(label)}:\s*" rf"([\d.]+)\s+seconds\s*$", output, re.MULTILINE)
    return float(match.group(1)) if match else None

def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}", flush=True)

def run_inference_test(number):
    print(f"\n--- REPEATABILITY RUN {number}/{REPETITIONS} ---")
    started = time.perf_counter()
    result = subprocess.run([sys.executable, "-m", "scripts.test_e2e_02_inference"], capture_output=True, text=True, check=False)
    wall_time = time.perf_counter() - started
    output = result.stdout + "\n" + result.stderr

    if result.returncode != 0:
        print(output[-6000:])
        raise RuntimeError(f"Read-only inference run {number} failed.")

    check("E2E-02 RESULT: PASS" in output, f"Run {number}: inference integration passed")

    locations = extract_number(output, "Locations processed")
    predictions = extract_number(output, "Predictions generated")
    generic_rows = extract_number(output, "Best Match hourly rows")
    ecmwf_rows = extract_number(output, "ECMWF hourly rows")
    check(locations == EXPECTED_LOCATIONS, f"Run {number}: all 237 locations processed")
    check(predictions == EXPECTED_LOCATIONS, f"Run {number}: all predictions generated")
    check(generic_rows == EXPECTED_HOURLY_ROWS and ecmwf_rows == EXPECTED_HOURLY_ROWS, f"Run {number}: expected weather rows acquired")

    durations = {stage: extract_duration(output, stage) for stage in STAGES}
    check(all(value is not None for value in durations.values()), f"Run {number}: all stage timings recorded")

    recorded_total = extract_duration(output, "Total test time")
    check(recorded_total is not None, f"Run {number}: total test duration recorded")

    print(f"Recorded test time: {recorded_total:.2f}s")
    print(f"Acquisition: {durations['Weather acquisition']:.2f}s")
    print(f"Feature generation: {durations['Weather features']:.2f}s")
    print(f"Model inference: {durations['Model inference']:.2f}s")

    return {
        "run": number,
        "locations": locations,
        "predictions": predictions,
        "generic_hourly_rows": generic_rows,
        "ecmwf_hourly_rows": ecmwf_rows,
        "recorded_test_seconds": recorded_total,
        "process_wall_seconds": round(wall_time, 2),
        "stage_durations": durations,
    }

def summary(values):
    return {
        "minimum": round(min(values), 2),
        "median": round(statistics.median(values), 2),
        "maximum": round(max(values), 2),
    }

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-06B")
    print("=" * 50)
    print("Mode: REPEATED READ-ONLY OPERATIONAL WORKFLOW")

    results = []

    for number in range(1, REPETITIONS + 1):
        results.append(run_inference_test(number))

    print("\n--- PERFORMANCE SUMMARY ---")
    total_times = [result["recorded_test_seconds"] for result in results]
    stage_summaries = {}

    for stage in STAGES:
        values = [result["stage_durations"][stage] for result in results]
        stage_summaries[stage] = summary(values)

        print(
            f"{stage}: "
            f"median={stage_summaries[stage]['median']}s, "
            f"min={stage_summaries[stage]['minimum']}s, "
            f"max={stage_summaries[stage]['maximum']}s"
        )

    total_summary = summary(total_times)

    print("\nRecorded total test duration:")
    print(f"Minimum: {total_summary['minimum']}s")
    print(f"Median: {total_summary['median']}s")
    print(f"Maximum: {total_summary['maximum']}s")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d_%H%M%S")
    evidence_path = (OUTPUT_DIR / f"e2e_06b_repeatability_{timestamp}.json")
    evidence = {
        "test_id": "E2E-06B",
        "executed_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
        "repetitions": REPETITIONS,
        "read_only": True,
        "successful_runs": len(results),
        "expected_locations": EXPECTED_LOCATIONS,
        "results": results,
        "stage_summaries": stage_summaries,
        "total_duration_summary": total_summary,
    }

    with evidence_path.open("w", encoding="utf-8") as file:
        json.dump(evidence, file, indent=2)
    print("\nEvidence saved:", evidence_path)

    print("\n" + "=" * 50)
    print("E2E-06B RESULT: PASS")
    print("=" * 50)
    print(f"Successful read-only runs: " f"{len(results)}/{REPETITIONS}")
    print("All runs produced valid model predictions for 237 locations.")
    print("No operational predictions, weather records or alerts were persisted.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-06B RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)