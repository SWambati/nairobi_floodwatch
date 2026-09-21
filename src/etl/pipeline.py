import os
import subprocess
import sys
import time
from pathlib import Path
import pandas as pd
from config.paths import PROCESSED_DIR


PIPELINE_REPORT_DIR = (PROCESSED_DIR/ "pipeline")


TRANSFORM_STAGES = [
    (
        "flood_label_preprocessing",
        "scripts.process_flood_labels",
    ),
    (
        "chirps_rainfall_extraction",
        "scripts.process_chirps",
    ),
    (
        "chirps_feature_engineering",
        "scripts.process_chirps_features",
    ),
    (
        "era5_land_preprocessing",
        "scripts.process_era5_land",
    ),
    (
        "era5_feature_engineering",
        "scripts.process_era5_features",
    ),
    (
        "gis_feature_extraction",
        "scripts.process_gis_features",
    ),
    (
        "administrative_spatial_joins",
        "scripts.process_admin_spatial",
    ),
    (
        "etl_validation",
        "scripts.validate_etl",
    ),
]


LOAD_STAGES = [
    (
        "database_connection_check",
        "scripts.check_database",
    ),
    (
        "postgresql_loading",
        "scripts.load_etl_to_postgres",
    ),
]


def run_stage(stage_name: str,  module_name: str) -> dict:

    print()
    print("=" * 72)
    print(f"STAGE: {stage_name}")
    print(f"MODULE: {module_name}")
    print("=" * 72)

    start_time = time.perf_counter()

    environment = os.environ.copy()

    existing_pythonpath = environment.get("PYTHONPATH","")

    environment["PYTHONPATH"] = (
        "."
        if not existing_pythonpath
        else f".{os.pathsep}{existing_pythonpath}"
    )

    process = subprocess.run([sys.executable, "-m", module_name,], env=environment,)

    end_time = time.perf_counter()
    duration_seconds = (end_time - start_time)

    status = (
        "PASS"
        if process.returncode == 0
        else "FAIL"
    )

    result = {
        "stage": stage_name,
        "module":  module_name,
        "status": status,
        "return_code": process.returncode,
        "duration_seconds": round(duration_seconds, 2,),
    }

    print()
    print(
        f"{stage_name}: "
        f"{status} "
        f"({duration_seconds:.2f} seconds)"
    )

    return result


def run_stage_group(stages: list[tuple[str, str]], results: list[dict],) -> None:

    for (stage_name, module_name,) in stages:
        result = run_stage(stage_name,module_name,)
        results.append(result)

        if result["status"] == "FAIL":
            raise RuntimeError(f"Historical ETL pipeline stopped at stage: {stage_name}")


def run_historical_pipeline(phase: str) -> pd.DataFrame:

    valid_phases = {
        "transform",
        "load",
        "all",
    }

    if phase not in valid_phases:
        raise ValueError(f"Invalid historical ETL phase. Expected one of: {sorted(valid_phases)}")

    results = []

    pipeline_start = (time.perf_counter())

    try:
        if phase in {"transform", "all",}:
            run_stage_group(TRANSFORM_STAGES, results,)

        if phase in {"load", "all",}:
            run_stage_group(LOAD_STAGES, results,)

    finally:

        pipeline_end = (time.perf_counter())
        total_seconds = (pipeline_end - pipeline_start)

        report = pd.DataFrame(results)

        if not report.empty:
            report["pipeline_phase"] = phase
            report["pipeline_total_seconds"] = round(total_seconds, 2,)
            save_pipeline_report(report, phase)
    return report


def save_pipeline_report(report: pd.DataFrame, phase: str) -> None:

    PIPELINE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = (PIPELINE_REPORT_DIR/ f"historical_etl_{phase}_report.csv")
    report.to_csv(report_file, index=False)
    print()
    print(f"Saved pipeline report: {report_file}")