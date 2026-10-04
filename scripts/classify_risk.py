import argparse
from pathlib import Path
import pandas as pd
from src.model.risk_classification import FloodRiskClassifier

def parse_arguments():
    parser = argparse.ArgumentParser(description="Convert Nairobi FloodWatch Model Scores into risk levels")

    parser.add_argument("--input", required=True, help="CSV containing a flood_probability column")
    parser.add_argument("--output", default=None, help="Optional output CSV path")

    return parser.parse_args()

def main():
    args = parse_arguments()

    input_file = Path(args.input)

    if not input_file.exists():
        raise FileNotFoundError(f"Risk classification input not found: {input_file}")

    if input_file.suffix.lower() != ".csv":
        raise ValueError("Risk classification input must be a CSV file")

    if args.output:
        output_file = Path(args.output)

    else:
        output_file = input_file.with_name(f"{input_file.stem}_risk.csv")

    dataframe = pd.read_csv(input_file)

    classifier = FloodRiskClassifier()
    classified = classifier.classify_batch(dataframe)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    classified.to_csv(output_file, index=False)

    counts = classified['risk_level'].value_counts()

    low_count = int(counts.get('LOW', 0))
    moderate_count = int(counts.get('MODERATE', 0))
    high_count = int(counts.get('HIGH', 0))

    print("Nairobi FloodWatch Risk Classification")

    print("=" * 40)

    print(f"Input Rows: {len(classified):,}")
    print()

    print("Risk Thresholds: ")
    print(f"LOW: score < {classifier.moderate_threshold:.4f}")
    print(f"MODERATE: {classifier.moderate_threshold:.4f} <= score < {classifier.high_threshold:.4f}")
    print(f"HIGH: score >= {classifier.high_threshold:.4f}")
    print()

    if low_count + moderate_count + high_count != len(classified):
        print("Risk distribution counts do not match the number of classified rows")

    print("Risk distribution: ")
    print(f"LOW: {low_count:,}")
    print(f"MODERATE: {moderate_count:,}")
    print(f"HIGH: {high_count:,}")
    print()

    print(f"Saved classifications: {output_file}")
    print()
    print("Flood risk classifications: OK")

if __name__ == "__main__":
    main()
