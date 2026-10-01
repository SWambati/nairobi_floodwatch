import hashlib
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd

from config.paths import PROCESSED_DIR
from src.model.model_dataset import PREDICTOR_COLUMNS


#Paths
PROJECT_OUTPUT_DIR = PROCESSED_DIR.parent
FINAL_MODEL_DIR = PROJECT_OUTPUT_DIR/ "models" / "final"
MODEL_FILE = FINAL_MODEL_DIR / "random_forest_final.joblib"
MODEL_METADATA_FILE = FINAL_MODEL_DIR / "final_model_metadata.json"
INFERENCE_OUTPUT_DIR = PROCESSED_DIR / "modeling" / "inference"

#Generic Helpers
def calculate_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(block)

    return sha256.hexdigest()

class FloodInferenceEngine:
    def __init__(self, model_path: Path = MODEL_FILE, metadata_path: Path = MODEL_METADATA_FILE):
        self.model_path = Path(model_path)
        self.metadata_path = Path(metadata_path)

        if not self.model_path.exists():
            raise FileNotFoundError(f"Final Random Forest model was not found: {self.model_path}")

        if not self.metadata_path.exists():
            raise FileNotFoundError(f"Final model metadata file was not found: {self.metadata_path}")

        with self.metadata_path.open("r", encoding="utf-8") as file:
            self.metadata = json.load(file)

        self.predictor_columns = list(self.metadata.get("predictor_columns", []))

        if self.predictor_columns != list(PREDICTOR_COLUMNS):
            raise ValueError("Final model metadata predictor schema does not match the current model schema")

        self.threshold = float(self.metadata.get("decision_threshold", 0.50))

        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("Model decision threshold must be between 0 and 1")

        self.model_sha256 = calculate_sha256(self.model_path)
        expected_sha256 = self.metadata.get("production_model_sha256")

        if expected_sha256 and self.model_sha256 != expected_sha256:
            raise ValueError("Final model checksum does not match the checksum stored in metadata")

        self.model = joblib.load(self.model_path)

        if not hasattr(self.model, "predict_proba"):
            raise TypeError("Loaded production model does not support probability prediction")

        if getattr(self.model, "n_features_in_", None) != len(self.predictor_columns):
            raise ValueError("Loaded model feature count does not match the predictor schema")

        if hasattr(self.model, "feature_names_in_"):
            model_features = list(self.model.feature_names_in_)

            if model_features != self.predictor_columns:
                raise ValueError("Loaded model feature names do not match the expected predictor schema")

        classes = np.asarray(self.model.classes_)
        positive_indices = np.flatnonzero(classes == 1)

        if len(positive_indices) != 1:
            raise ValueError("Production model does not contain exactly one flood-positive class")

        self.positive_class_index = int(positive_indices[0])
        self.model_name = self.metadata.get("model_name", "nairobi_floodwatch_random_forest")
        self.model_version = self.model_sha256[:12]


    def _prepare_features(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        if dataframe.empty:
            raise ValueError("Inference input contains no rows")

        if dataframe.columns.duplicated().any():
            raise ValueError("Inference input contains duplicate column names")

        missing = set(self.predictor_columns) - set(dataframe.columns)

        if missing:
            raise ValueError(f"Inference input is missing required predictor columns: {sorted(missing)}")

        features = dataframe[self.predictor_columns].copy()

        for column in self.predictor_columns:
            features[column] = pd.to_numeric(features[column], errors="coerce")

        if features.isna().any().any():
            missing_values = features.isna().sum()
            missing_values = missing_values[missing_values > 0]

            raise ValueError(f"Inference input contains missing or non-numeric predictor values: {missing_values}")

        values = features.to_numpy(dtype=float)

        if not np.isfinite(values).all():
            raise ValueError("Inference input contains non-finite predictor values")

        return features

    def predict_batch(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        features = self._prepare_features(dataframe)

        probabilities = self.model.predict_proba(features)[:, self.positive_class_index]
        predictions = (probabilities >= self.threshold).astype(int)

        metatdata_columns = [column for column in dataframe.columns if column not in self.predictor_columns]

        output = dataframe[metatdata_columns].copy()

        output["flood_probability"] = probabilities
        output["predicted_label"] = predictions
        output["decision_threshold"] = self.threshold
        output["model_name"] = self.model_name
        output["model_version"] = self.model_version

        return output

    def model_info(self) -> dict:
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "model_sha256": self.model_sha256,
            "predictor_count": len(self.predictor_columns),
            "predictor_columns": self.predictor_columns,
            "decision_threshold": self.threshold
        }




