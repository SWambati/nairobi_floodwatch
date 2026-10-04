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

    low_count = int(counts.get('low', 0))
    medium_count = int(counts.get('medium', 0))
    high_count = int(counts.get('high', 0))

    print("Nairobi FloodWatch Risk Classification")

    print("=" * 40)

    print(f"Input Rows: {len(classified):,}")
    print()

    print("Risk Thresholds: ")
    print(f"Low: score < {classifier.moderate_threshold:.4f}")
    print(f"Medium: {classifier.moderate_threshold:.4f} <= score < {classifier.high_threshold:.4f}")
    print(f"High: score >= {classifier.high_threshold:.4f}")
    print()

    print("Risk distribution: ")
    print(f"Low: {low_count:,}")
    print(f"Medium: {medium_count:,}")
    print(f"High: {high_count:,}")
    print()

    print(f"Saved classifications: {output_file}")
    print()
    print("Flood risk classifications: OK")

if __name__ == "__main__":
    main()
    