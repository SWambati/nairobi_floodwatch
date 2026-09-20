from src.etl.validation import (run_etl_validation, save_validation_report)

def main():

    print("Nairobi FloodWatch ETL Validation")

    print("-" * 60)

    print("Validating outputs from the historical ETL pipeline...")

    report = run_etl_validation()

    print("\nValidation results:")

    for row in report.itertuples(index=False):
        print(f"{row.status:4} | {row.check:25} | {row.detail}")

    passed = int((report["status"] == "PASS").sum())
    failed = int((report["status"] == "FAIL").sum())

    print("\nValidation summary:" )
    print(f"Passed: {passed:,}")
    print(f"Failed: {failed:,}")
    save_validation_report(report)

    if failed > 0:
        failed_checks = (report.loc[report["status"] == "FAIL", "check",].tolist())
        raise RuntimeError(f"ETL validation failed for: {failed_checks}")

    print("\nETL validation: OK")


if __name__ == "__main__":
    main()