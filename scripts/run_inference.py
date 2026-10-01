import argparse
import time
from pathlib import Path
import pandas as pd
from src.model.inference import FloodInferenceEngine, INFERENCE_OUTPUT_DIR

def parse_arguments():
    parser = argparse.ArgumentParser(description="Run Nairobi FloodWatch Random Forest inference")

    parser.add_argument("--input", required=True, help="CSV containing the 15 required model predictors.")
    parser.add_argument("--output", default=None, help="Optional output CSV path")

    return parser.parse_args()

def main():
    args = parse_arguments()

    input_file = Path(args.input)

    if not input_file.exists():
        raise FileNotFoundError(f"Inference input file not found: {input_file}")

    if input_file.suffix.lower() != ".csv":
        raise ValueError("Inference input must be a CSV file")

    if args.output:
        output_file = Path(args.output)

    else:
        INFERENCE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_file = INFERENCE_OUTPUT_DIR/ f"{input_file.stem}_predictions.csv"

    dataframe = pd.read_csv(input_file)
    engine = FloodInferenceEngine()

    start = time.perf_counter()
    predictions = engine.predict_batch(dataframe)
    inference_seconds = time.perf_counter() - start

    output_file.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output_file, index=False)

    flood_count = int((predictions["predicted_label"] == 1).sum())
    non_flood_count = int((predictions["predicted_label"] == 0).sum())

    print("Nairobi FloodWatch Production Inference")

    print("-" * 40)

    print(f"Input rows: {len(dataframe):,}")
    print(f"Predictor count: {len(engine.predictor_columns)}")
    print(f"Model: {engine.model_name}")
    print(f"Model version: {engine.model_version}")
    print(f"Decision threshold: {engine.threshold:.2f}")
    print()

    print("Inference results: ")
    print(f"Predicted Flood: {flood_count:,}")
    print(f"Predicted non-flood: {non_flood_count:,}")
    print(f"Minimum probability: {predictions['flood_probability'].min():.4f}")
    print(f"Mean probability: {predictions['flood_probability'].mean():.4f}")
    print(f"Maximum probability: {predictions['flood_probability'].max():.4f}")
    print()

    print(f"Total inference time: {inference_seconds:.6f} seconds")
    print(f"Inference per row: {(inference_seconds / len(dataframe)) * 1000:.4f} ms")

    print()

    print(f"Saved predictions: {output_file}")
    print()
    print("Production inference: OK")

if __name__ == "__main__":
    main()


