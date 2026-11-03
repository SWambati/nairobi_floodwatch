import json
import math
import statistics
import sys
import time

from concurrent.futures import (ThreadPoolExecutor, as_completed)
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

BASE_URL = "http://127.0.0.1:8010"

EXPECTED_LOCATIONS = 237
SAMPLES_PER_ENDPOINT = 20
CONCURRENT_WORKERS = 4
CONCURRENT_REQUESTS = 16

TIMEOUT = (3, 15)

OUTPUT_DIR = (Path("outputs") / "project_outputs" / "testing")

def check(condition, message):
    if not condition:
        raise AssertionError(message)

    print(f"[PASS] {message}", flush=True)

def percentile(values, percent):
    ordered = sorted(values)
    if not ordered:
        return None
    index = math.ceil(len(ordered) * percent / 100) - 1
    return ordered[max(0, index)]

def validate_response(response, kind):
    #Validate that successful HTTP responses also contain the expected data structure.
    if response.status_code != 200:
        return False, (f"Unexpected HTTP {response.status_code}")

    try:
        if kind == "page":
            if not response.text.strip():
                return False, "Empty HTML response"
        elif kind == "overview":
            data = response.json()
            if not isinstance(data, dict):
                return False, "Invalid overview structure"

            if not data.get("latest_prediction"):
                return False, "Latest prediction missing"

        elif kind == "locations":
            data = response.json()
            if not isinstance(data, list):
                return False, "Invalid location list"

            if len(data) != EXPECTED_LOCATIONS:
                return False, (f"Expected {EXPECTED_LOCATIONS} " f"locations, received {len(data)}")

            ids = [str(row["location_id"]) for row in data]

            if len(set(ids)) != len(ids):
                return False, "Duplicate monitoring points"

        elif kind == "history":
            data = response.json()

            if not isinstance(data, dict):
                return False, "Invalid history structure"

            predictions = data.get("predictions")

            if not isinstance(predictions, list):
                return False, "Invalid prediction history"

            if not predictions:
                return False, "No historical predictions"

    except (ValueError, KeyError, TypeError) as exc:
        return False, f"Invalid response: {type(exc).__name__}"

    return True, None

def request_once(name, path, kind):
    #Measure the time required to obtain and validate a complete HTTP response.
    #Uses a fresh request, with no login cookies.
    started = time.perf_counter()

    try:
        response = requests.get(
            BASE_URL + path,
            timeout=TIMEOUT,
            allow_redirects=False,
            headers={"Accept": ("text/html" if kind == "page" else "application/json"), "Cache-Control": "no-cache"},
        )

        elapsed_ms = (time.perf_counter() - started) * 1000
        valid, error = validate_response(response, kind)

        return {
            "endpoint": name,
            "elapsed_ms": round(elapsed_ms, 2),
            "status_code": response.status_code,
            "success": valid,
            "error": error,
        }

    except requests.RequestException as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "endpoint": name,
            "elapsed_ms": round(elapsed_ms, 2),
            "status_code": None,
            "success": False,
            "error": type(exc).__name__,
        }

def summarize(results):
    successful = [row for row in results if row["success"]]
    latencies = [row["elapsed_ms"] for row in successful]

    return {
        "requests": len(results),
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "success_rate_percent": round(100 * len(successful) / len(results), 2) if results else 0,
        "median_ms": round(statistics.median(latencies), 2) if latencies else None,
        "p95_ms": round(percentile(latencies, 95), 2) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
    }

def print_summary(name, summary):
    print(f"\n{name}")
    print("-" * 50)
    print("Requests:", summary["requests"])
    print("Successful:", summary["successful"])
    print("Failed:", summary["failed"])
    print("Success rate:", f"{summary['success_rate_percent']:.2f}%")
    print("Median:", summary["median_ms"], "ms")
    print("P95:", summary["p95_ms"], "ms")
    print("Maximum:", summary["max_ms"], "ms")

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-06A")
    print("=" * 50)
    print("Mode: READ-ONLY API PERFORMANCE")
    print("Server:", BASE_URL)

    started = time.perf_counter()

    #Preflight
    print("\n--- A. PREFLIGHT ---")

    try:
        response = requests.get(BASE_URL + "/api/locations", timeout=TIMEOUT)
        response.raise_for_status()
        locations = response.json()

    except requests.RequestException as exc:
        raise RuntimeError("FastAPI is unavailable. Start Uvicorn on port 8010.") from exc

    check(isinstance(locations, list) and len(locations) == EXPECTED_LOCATIONS, "Monitoring API returns all 237 locations")

    ids = [str(row["location_id"]) for row in locations]
    check(len(set(ids)) == EXPECTED_LOCATIONS, "All monitoring-location IDs are unique")

    sample_id = ids[len(ids) // 2]
    endpoints = [
        ("Home", "/", "page"),
        ("Resident page", "/resident", "page"),
        ("Monitoring overview", "/api/overview", "overview"),
        ("Monitoring locations", "/api/locations", "locations"),
        ("Location history", ("/api/resident/locations/" f"{sample_id}/history?days=30"), "history")
    ]

    #Repeated API requests
    print("\n--- B. REPEATED RESPONSE MEASUREMENTS ---")
    endpoint_summaries = {}
    all_results = []

    for name, path, kind in endpoints:
        # One warm-up request, not counted.
        request_once(name, path, kind)
        measurements = []

        for _ in range(SAMPLES_PER_ENDPOINT):
            measurements.append(request_once(name, path, kind))

        summary = summarize(measurements)
        endpoint_summaries[name] = summary
        all_results.extend(measurements)
        print_summary(name, summary)

    #Light concurrent requests
    print("\n--- LIGHT CONCURRENCY TEST ---")
    jobs = [endpoints[index % len(endpoints)] for index in range(CONCURRENT_REQUESTS)]
    concurrent_results = []
    concurrent_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        futures = [executor.submit(request_once, name, path, kind) for name, path, kind in jobs]

        for future in as_completed(futures):
            concurrent_results.append(
                future.result())
    concurrent_elapsed = (time.perf_counter() - concurrent_start)
    concurrent_summary = summarize(concurrent_results)
    concurrent_summary["wall_time_seconds"] = round(concurrent_elapsed, 2)
    concurrent_summary["observed_requests_per_second"] = round(CONCURRENT_REQUESTS / concurrent_elapsed, 2) if concurrent_elapsed else None

    print_summary("Concurrent HTTP requests", concurrent_summary)
    print(
        "Observed request throughput:",
        concurrent_summary["observed_requests_per_second"],
        "requests/second"
    )

    #Combined result
    all_results.extend(concurrent_results)
    combined = summarize(all_results)
    print_summary("OVERALL API RELIABILITY", combined)

    #Save reproducible evidence
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d_%H%M%S")
    evidence_path = (OUTPUT_DIR / f"e2e_06a_api_performance_{timestamp}.json")
    evidence = {
        "test_id": "E2E-06A",
        "executed_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
        "server": BASE_URL,
        "expected_locations": EXPECTED_LOCATIONS,
        "samples_per_endpoint": SAMPLES_PER_ENDPOINT,
        "concurrent_workers": CONCURRENT_WORKERS,
        "endpoint_results": endpoint_summaries,
        "concurrency": concurrent_summary,
        "overall": combined,
        "duration_seconds": round(time.perf_counter() - started, 2)
    }

    with evidence_path.open("w", encoding="utf-8") as file:
        json.dump(evidence, file, indent=2)
    print("\nEvidence saved:", evidence_path)

    #Final assertions
    print("\n" + "=" * 50)

    if combined["failed"] != 0:
        print("E2E-06A RESULT: FAIL")
        for row in all_results:
            if not row["success"]:
                print(f" - {row['endpoint']}: " f"{row['error']}")
        return 1

    print("E2E-06A RESULT: PASS")
    print("=" * 50)
    print("All sampled API requests completed with valid responses.")
    print("No database records or user accounts were modified.")
    print("Performance timings are local benchmark observations, not production guarantees.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("\nE2E-06A RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
