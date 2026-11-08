"""
TC-015A: FloodWatch Production API Performance
Target: Deployed FastAPI application on Render.
Measures:
    - First observed HTTP request
    - Mean response time
    - Median response time
    - 95th percentile (nearest-rank)
    - Minimum and maximum response times
    - HTTP success rate
    - Response structure validity
Method: 12 sequential samples per endpoint, following one uncounted warm-up request per endpoint.
Safety:
    - GET requests only
    - No concurrency
    - No database modifications
    - No weather API acquisition

Note: Measurements are client-observed HTTP latencies not internal FastAPI processing times.
"""
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import requests

BASE_URL = os.getenv("FLOODWATCH_BASE_URL", "https://nairobi-floodwatch.onrender.com").rstrip("/")
EXPECTED_LOCATIONS = 237
SAMPLES = 12
PAUSE_SECONDS = 0.5
TIMEOUT = (15, 60)
OUTPUT = Path("outputs/testing/tc015_api_performance.json")

session = requests.Session()
session.headers.update({"User-Agent": "FloodWatch-TC015-Performance", "Cache-Control": "no-cache"})

def validate(response, kind):
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"

    try:
        if kind == "page":
            if "<html" not in response.text.lower():
                return False, "Expected HTML page"

        elif kind == "overview":
            payload = response.json()
            if not isinstance(payload, dict):
                return False, "Invalid overview JSON"

            if not payload.get("latest_prediction"):
                return False, "Missing assessment timestamp"

        elif kind == "locations":
            payload = response.json()
            if not isinstance(payload, list):
                return False, "Expected location list"

            ids = [row["location_id"] for row in payload]

            if len(ids) != EXPECTED_LOCATIONS:
                return False, "Incorrect location count"

            if len(set(ids)) != len(ids):
                return False, "Duplicate location IDs"

        elif kind == "history":
            payload = response.json()
            predictions = payload.get("predictions")

            if not isinstance(predictions, list):
                return False, "Invalid history format"

            if not predictions:
                return False, "Empty prediction history"

        return True, None

    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return False, (f"Invalid response: {type(exc).__name__}")

def measure(path, kind):
    started = time.perf_counter()
    try:
        response = session.get(
            BASE_URL + path,
            timeout=TIMEOUT,
            allow_redirects=False,
            headers={"Accept": ("text/html" if kind == "page" else "application/json")}
        )
        elapsed = (time.perf_counter() - started) * 1000
        success, error = validate(response, kind)

        return {
            "elapsed_ms": round(elapsed, 2),
            "status_code": response.status_code,
            "response_bytes": len(response.content),
            "success": success,
            "error": error
        }

    except requests.RequestException as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return {
            "elapsed_ms": round(elapsed, 2),
            "status_code": None,
            "response_bytes": 0,
            "success": False,
            "error": type(exc).__name__
        }

def percentile(values, percentage):
    ordered = sorted(values)

    if not ordered:
        return None

    index = math.ceil(len(ordered) * percentage / 100) - 1
    index = max(0, min(index, len(ordered) - 1))

    return ordered[index]

def summarize(samples):
    successful = [sample for sample in samples if sample["success"]]
    durations = [sample["elapsed_ms"] for sample in successful]

    return {
        "requests": len(samples),
        "successful": len(successful),
        "failed": len(samples) - len(successful),
        "success_rate_percent": round(100 * len(successful) / len(samples), 2) if samples else 0,
        "mean_ms": round(statistics.mean(durations), 2) if durations else None,
        "median_ms": round(statistics.median(durations), 2) if durations else None,
        "p95_ms": percentile(durations, 95),
        "minimum_ms": min(durations) if durations else None,
        "maximum_ms": max(durations) if durations else None
    }

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH - TC-015A")
    print("PRODUCTION API PERFORMANCE")
    print("=" * 50)
    print("Target:", BASE_URL)
    print("Requests per endpoint:", SAMPLES)
    print("Concurrency: NONE")
    print("Mode: READ-ONLY")
    print()

    #First observed request, measured separately.
    #It is not necessarily a true Render cold start.
    print("--- FIRST OBSERVED REQUEST ---")
    first_request = measure("/api/overview", "overview")
    print("Overview:", first_request["elapsed_ms"], "ms")
    print("Status:", first_request["status_code"])

    #Determine a real monitoring location for history.
    print("\n--- PREFLIGHT ---")
    response = session.get(BASE_URL + "/api/locations", timeout=TIMEOUT)
    response.raise_for_status()
    locations = response.json()

    if (not isinstance(locations, list) or len(locations) != EXPECTED_LOCATIONS):
        raise RuntimeError("Production location preflight failed.")

    sample_id = locations[len(locations) // 2]["location_id"]

    print("Monitoring locations:", len(locations))
    print("History sample location ID:", sample_id)

    endpoints = [
        ("Homepage", "/", "page"),
        ("Resident dashboard", "/resident", "page"),
        ("Monitoring overview", "/api/overview", "overview"),
        ("Monitoring locations", "/api/locations", "locations"),
        ("Location history", f"/api/resident/locations/{sample_id}/history?days=30", "history")
    ]

    evidence = {
        "test_id": "TC-015A",
        "target": BASE_URL,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "sequential_http_requests",
        "samples_per_endpoint": SAMPLES,
        "pause_seconds": PAUSE_SECONDS,
        "first_observed_request": first_request,
        "history_sample_location_id": sample_id,
        "endpoints": {},
    }

    total_failures = 0

    print("\n--- ENDPOINT MEASUREMENTS ---")
    for name, path, kind in endpoints:
        print(f"\n{name}")
        print("-" * 50)

        #Warm-up is saved but excluded from statistics.
        warmup = measure(path, kind)
        time.sleep(PAUSE_SECONDS)

        samples = []

        for number in range(1, SAMPLES + 1):
            result = measure(path, kind)
            samples.append(result)
            status = ("PASS" if result["success"] else "FAIL")

            print(f"  {number:02d}. [{status}] {result['elapsed_ms']:.2f} ms | HTTP {result['status_code']}")

            time.sleep(PAUSE_SECONDS)

        summary = summarize(samples)
        total_failures += summary["failed"]

        evidence["endpoints"][name] = {
            "path": path,
            "warmup": warmup,
            "samples": samples,
            "summary": summary
        }

        print()
        print("Mean:", summary["mean_ms"], "ms")
        print("Median:", summary["median_ms"], "ms")
        print("P95:", summary["p95_ms"], "ms")
        print("Maximum:", summary["maximum_ms"], "ms")
        print("Successful:", summary["successful"], "/", summary["requests"])

    #Save evidence even when some requests failed.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    print("\n" + "=" * 50)
    print("TC-015A PERFORMANCE SUMMARY")
    print("=" * 50)
    print("First observed request:", first_request["elapsed_ms"], "ms")
    print("Measured endpoint requests:", len(endpoints) * SAMPLES)
    print("Failed measured requests:", total_failures)
    print("Evidence:", OUTPUT)

    if total_failures or not first_request["success"]:
        print("RESULT: FAIL - request reliability")
        sys.exit(1)

    print("RESULT: REQUEST RELIABILITY PASS")
    print("Latency acceptance requires comparison against documented performance requirements.")
    print("P95 is indicative only because each endpoint has a small sample of 12 requests.")

if __name__ == "__main__":
    main()
