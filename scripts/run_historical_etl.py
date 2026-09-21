import argparse
from src.etl.pipeline import (run_historical_pipeline)


def parse_arguments():

    parser = argparse.ArgumentParser(description=("Run the Nairobi FloodWatch historical ETL pipeline"))
    parser.add_argument(
        "--phase",
        choices=[ "transform", "load", "all",],
        required=True,
        help=("transform: run historical ETL through validation; load: load validated outputs into PostgreSQL; all: run both phases" ),)

    return parser.parse_args()


def main():
    arguments = (parse_arguments())

    print("Nairobi FloodWatch Historical ETL Pipeline")

    print("=" * 72)

    print(f"Pipeline phase: {arguments.phase}")

    report = (run_historical_pipeline(arguments.phase))

    print()
    print("=" * 72)
    print("PIPELINE SUMMARY")
    print("=" * 72)

    for row in report.itertuples(index=False):
        print(f"{row.status:4} | {row.stage:35} | {row.duration_seconds:10.2f} s")

    passed = int((report["status"] == "PASS").sum())
    failed = int((report["status"] == "FAIL").sum())
    total_seconds = (report["duration_seconds"].sum())

    print()
    print(f"Stages passed: {passed:,}")

    print(f"Stages failed: {failed:,}")

    print(f"Total stage execution time: {total_seconds:.2f} seconds")

    if failed > 0:
        raise RuntimeError("Historical ETL pipeline did not complete successfully")

    print()
    print("Historical ETL pipeline: OK")


if __name__ == "__main__":
    main()