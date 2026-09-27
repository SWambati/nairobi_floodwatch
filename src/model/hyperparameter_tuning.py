import hashlib
import json
import time
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    auc,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import ParameterSampler
from xgboost import XGBClassifier
from config.paths import PROCESSED_DIR
from src.model.model_dataset import PREDICTOR_COLUMNS

# Configuration

RANDOM_SEED = 42
RF_SEARCH_ITERATIONS = 40
XGB_SEARCH_ITERATIONS = 40
DECISION_THRESHOLD = 0.5

# Hyperparameter search spaces

RF_PARAMETER_DISTRIBUTIONS = {
    'n_estimators': [200, 300, 500, 700],
    'max_depth': [None, 5, 8, 12, 16, 20],
    'min_samples_split': [2, 4, 6, 10, 15],
    'min_samples_leaf': [1, 2, 3, 4, 6, 8],
    'max_features': ['sqrt', 'log2', 0.5, 0.75],
    'bootstrap': [True],
}
XGB_PARAMETER_DISTRIBUTIONS = {
    'n_estimators': [100, 200, 300, 500, 700],
    'learning_rate': [0.01, 0.03, 0.05, 0.1, 0.2],
    'max_depth': [2, 3, 4, 5, 6],
    'min_child_weight': [1, 2, 4, 6, 8],
    'gamma': [0.0, 0.1, 0.25, 0.5, 1.0],
    'subsample': [0.6, 0.75, 0.9, 1.0],
    'colsample_bytree': [0.6, 0.75, 0.9, 1.0],
    'reg_alpha': [0.0, 0.01, 0.1, 0.5, 1.0],
    'reg_lambda': [0.5, 1.0, 2.0, 5.0, 10.0],
}

# Paths

PROJECT_OUTPUT_DIR = PROCESSED_DIR.parent
FEATURES_DIR = PROJECT_OUTPUT_DIR / 'features'
SPLIT_DIR = FEATURES_DIR / 'modeling' / 'splits'
TRAIN_FILE = SPLIT_DIR / 'train_model_features.csv'
VALIDATION_FILE = SPLIT_DIR / 'validation_model_features.csv'

# Tuned model artifacts

RF_MODEL_DIR = PROJECT_OUTPUT_DIR / 'models' / 'random_forest' / 'tuned'
XGB_MODEL_DIR = PROJECT_OUTPUT_DIR / 'models' / 'xgboost' / 'tuned'
RF_MODEL_FILE = RF_MODEL_DIR / 'random_forest_tuned.joblib'
XGB_MODEL_FILE = XGB_MODEL_DIR / 'xgboost_tuned.json'
RF_METADATA_FILE = RF_MODEL_DIR / 'random_forest_tuned_metadata.json'
XGB_METADATA_FILE = XGB_MODEL_DIR / 'xgboost_tuned_metadata.json'

# Tuning reports

TUNING_REPORT_DIR = PROCESSED_DIR / 'modeling' / 'tuning'
RF_SEARCH_RESULTS_FILE = TUNING_REPORT_DIR / 'random_forest_search_results.csv'
XGB_SEARCH_RESULTS_FILE = TUNING_REPORT_DIR / 'xgboost_search_results.csv'
GROUP_DIAGNOSTIC_FILE = TUNING_REPORT_DIR / 'training_group_distribution.csv'
TUNED_METRICS_FILE = TUNING_REPORT_DIR / 'tuned_model_metrics.csv'
TUNED_COMPARISON_FILE = TUNING_REPORT_DIR / 'tuned_validation_comparison.csv'
BEST_PARAMS_FILE = TUNING_REPORT_DIR / 'best_hyperparameters.json'
RF_CONFUSION_MATRIX_FILE = TUNING_REPORT_DIR / 'random_forest_tuned_validation_confusion_matrix.csv'
XGB_CONFUSION_MATRIX_FILE = TUNING_REPORT_DIR / 'xgboost_tuned_validation_confusion_matrix.csv'
RF_FEATURE_IMPORTANCE_FILE = TUNING_REPORT_DIR / 'random_forest_tuned_feature_importance.csv'
XGB_FEATURE_IMPORTANCE_FILE = TUNING_REPORT_DIR / 'xgboost_tuned_feature_importance.csv'
RF_VALIDATION_PREDICTIONS_FILE = TUNING_REPORT_DIR / 'random_forest_tuned_validation_predictions.csv'
XGB_VALIDATION_PREDICTIONS_FILE = TUNING_REPORT_DIR / 'xgboost_tuned_validation_predictions.csv'

# Existing baseline metrics

RF_BASELINE_METRICS_FILE = PROCESSED_DIR / 'modeling' / 'random_forest' / 'baseline' / 'random_forest_baseline_metrics.csv'
XGB_BASELINE_METRICS_FILE = PROCESSED_DIR / 'modeling' / 'xgboost' / 'baseline' / 'xgboost_baseline_metrics.csv'

# Generic helpers


def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], dataset_name: str) -> None:
    missing = required_columns - set(dataframe.columns)
    if missing:
        raise ValueError(f'{dataset_name} is missing required columns: {sorted(missing)}')


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def calculate_file_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with file_path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            sha256.update(block)
    return sha256.hexdigest()

# Dataset loading


def load_model_splits() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(TRAIN_FILE)
    validation = pd.read_csv(VALIDATION_FILE)
    required_columns = {'model_record_id', 'model_split_group_id', 'flood_label', *PREDICTOR_COLUMNS}
    _require_columns(train, required_columns, 'Training dataset')
    _require_columns(validation, required_columns, 'Validation dataset')
    for dataset_name, dataframe in [('Training dataset', train), ('Validation dataset', validation)]:
        if dataframe['model_record_id'].duplicated().any():
            raise ValueError(f'{dataset_name} contains duplicate model IDs.')
        classes = set(dataframe['flood_label'].unique())
        if classes != {0, 1}:
            raise ValueError(f'{dataset_name} must contain both binary classes.')
        for column in PREDICTOR_COLUMNS:
            dataframe[column] = pd.to_numeric(dataframe[column], errors='coerce')
        if dataframe[PREDICTOR_COLUMNS].isna().any().any():
            missing = dataframe[PREDICTOR_COLUMNS].isna().sum()
            missing = missing[missing > 0]
            raise ValueError(f'{dataset_name} contains missing predictor values:\n{missing}')
        values = dataframe[PREDICTOR_COLUMNS].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f'{dataset_name} contains non-finite predictor values.')
    train_groups = set(train['model_split_group_id'])
    validation_groups = set(validation['model_split_group_id'])
    overlap = train_groups & validation_groups
    if overlap:
        raise ValueError('Training and validation group leakage detected.')
    return (train, validation)

# Training-group diagnostics


def build_group_diagnostic(train: pd.DataFrame) -> pd.DataFrame:
    diagnostic = train.groupby('model_split_group_id', as_index=False).agg(
        row_count=('model_record_id', 'size'),
        flood_rows=('flood_label', lambda values: int((values == 1).sum())),
        non_flood_rows=('flood_label', lambda values: int((values == 0).sum())),
    )
    diagnostic['balanced_group'] = diagnostic['flood_rows'] == diagnostic['non_flood_rows']
    if not diagnostic['balanced_group'].all():
        raise ValueError('Training event groups are not internally class balanced.')
    diagnostic = diagnostic.sort_values(by='row_count', ascending=False).reset_index(drop=True)
    return diagnostic

# Metric calculation


def calculate_metrics(y_true: pd.Series, probabilities: np.ndarray) -> tuple[dict, np.ndarray]:
    predictions = (probabilities >= DECISION_THRESHOLD).astype(int)
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1])
    tn, fp, fn, tp = matrix.ravel()
    precision_curve, recall_curve, _ = precision_recall_curve(y_true, probabilities)
    metrics = {
        'accuracy': accuracy_score(y_true, predictions),
        'precision': precision_score(y_true, predictions, zero_division=0),
        'recall': recall_score(y_true, predictions, zero_division=0),
        'f1': f1_score(y_true, predictions, zero_division=0),
        'roc_auc': roc_auc_score(y_true, probabilities),
        'pr_auc': auc(recall_curve, precision_curve),
        'average_precision': average_precision_score(y_true, probabilities),
        'true_negative': int(tn),
        'false_positive': int(fp),
        'false_negative': int(fn),
        'true_positive': int(tp),
    }
    return (metrics, predictions)

# Model builders


def build_random_forest(parameters: dict) -> RandomForestClassifier:
    return RandomForestClassifier(**parameters, random_state=RANDOM_SEED, class_weight=None, n_jobs=-1)


def build_xgboost(parameters: dict) -> XGBClassifier:
    return XGBClassifier(
        **parameters,
        objective='binary:logistic',
        eval_metric='logloss',
        tree_method='hist',
        scale_pos_weight=1.0,
        random_state=RANDOM_SEED,
        n_jobs=-1,
        verbosity=0,
    )

# Candidate evaluation


def evaluate_candidate(
    candidate_id: int,
    model_name: str,
    model,
    parameters: dict,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_validation: pd.DataFrame,
    y_validation: pd.Series,
) -> tuple[dict, dict]:
    training_start = time.perf_counter()
    model.fit(x_train, y_train)
    training_seconds = time.perf_counter() - training_start
    train_probabilities = model.predict_proba(x_train)[:, 1]
    train_metrics, train_predictions = calculate_metrics(y_train, train_probabilities)
    inference_start = time.perf_counter()
    validation_probabilities = model.predict_proba(x_validation)[:, 1]
    validation_inference_seconds = time.perf_counter() - inference_start
    validation_metrics, validation_predictions = calculate_metrics(y_validation, validation_probabilities)
    validation_inference_ms_per_row = validation_inference_seconds / len(x_validation) * 1000
    generalization_gap = train_metrics['f1'] - validation_metrics['f1']
    row = {
        'candidate_id': candidate_id,
        'model': model_name,
        'parameters_json': json.dumps(_json_safe(parameters), sort_keys=True),
        'training_seconds': training_seconds,
        'validation_inference_seconds': validation_inference_seconds,
        'validation_inference_ms_per_row': validation_inference_ms_per_row,
        'train_accuracy': train_metrics['accuracy'],
        'train_precision': train_metrics['precision'],
        'train_recall': train_metrics['recall'],
        'train_f1': train_metrics['f1'],
        'train_roc_auc': train_metrics['roc_auc'],
        'train_pr_auc': train_metrics['pr_auc'],
        'validation_accuracy': validation_metrics['accuracy'],
        'validation_precision': validation_metrics['precision'],
        'validation_recall': validation_metrics['recall'],
        'validation_f1': validation_metrics['f1'],
        'validation_roc_auc': validation_metrics['roc_auc'],
        'validation_pr_auc': validation_metrics['pr_auc'],
        'validation_average_precision': validation_metrics['average_precision'],
        'validation_true_negative': validation_metrics['true_negative'],
        'validation_false_positive': validation_metrics['false_positive'],
        'validation_false_negative': validation_metrics['false_negative'],
        'validation_true_positive': validation_metrics['true_positive'],
        'f1_generalization_gap': generalization_gap,
    }
    for key, value in parameters.items():
        row[f'param_{key}'] = value
    evaluation = {
        'model': model,
        'train_metrics': train_metrics,
        'validation_metrics': validation_metrics,
        'train_predictions': train_predictions,
        'validation_predictions': validation_predictions,
        'validation_probabilities': validation_probabilities,
        'training_seconds': training_seconds,
        'validation_inference_seconds': validation_inference_seconds,
        'validation_inference_ms_per_row': validation_inference_ms_per_row,
        'generalization_gap': generalization_gap,
        'parameters': parameters,
    }
    return (row, evaluation)

# Candidate selection


def _selection_key(evaluation: dict) -> tuple[float, float, float]:
    metrics = evaluation['validation_metrics']
    return (metrics['f1'], metrics['roc_auc'], metrics['pr_auc'])


def run_randomized_search(
    model_name: str,
    parameter_distributions: dict,
    iterations: int,
    model_builder,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    random_seed: int,
) -> dict:
    x_train = train[PREDICTOR_COLUMNS]
    y_train = train['flood_label'].astype(int)
    x_validation = validation[PREDICTOR_COLUMNS]
    y_validation = validation['flood_label'].astype(int)
    candidates = list(ParameterSampler(parameter_distributions, n_iter=iterations, random_state=random_seed))
    search_rows = []
    best_evaluation = None
    best_key = None
    best_candidate_id = None
    search_start = time.perf_counter()
    for candidate_number, parameters in enumerate(candidates, start=1):
        model = model_builder(parameters)
        row, evaluation = evaluate_candidate(
            candidate_id=candidate_number,
            model_name=model_name,
            model=model,
            parameters=parameters,
            x_train=x_train,
            y_train=y_train,
            x_validation=x_validation,
            y_validation=y_validation,
        )
        search_rows.append(row)
        key = _selection_key(evaluation)
        if best_key is None or key > best_key:
            best_key = key
            best_evaluation = evaluation
            best_candidate_id = candidate_number
    search_seconds = time.perf_counter() - search_start
    results = pd.DataFrame(search_rows)
    results = results.sort_values(
        by=['validation_f1', 'validation_roc_auc', 'validation_pr_auc', 'candidate_id'],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
    results.insert(0, 'selection_rank', np.arange(1, len(results) + 1))
    if best_evaluation is None:
        raise RuntimeError(f'No valid {model_name} candidate was selected.')
    return {
        'best_evaluation': best_evaluation,
        'best_candidate_id': best_candidate_id,
        'results': results,
        'search_seconds': search_seconds,
    }

# Feature importance


def build_rf_feature_importance(model: RandomForestClassifier) -> pd.DataFrame:
    result = pd.DataFrame({'feature_name': PREDICTOR_COLUMNS, 'importance': model.feature_importances_})
    result = result.sort_values(by='importance', ascending=False).reset_index(drop=True)
    result['rank'] = np.arange(1, len(result) + 1)
    return result[['rank', 'feature_name', 'importance']]


def build_xgb_feature_importance(model: XGBClassifier) -> pd.DataFrame:
    booster = model.get_booster()
    gain_scores = booster.get_score(importance_type='gain')
    rows = []
    for feature in PREDICTOR_COLUMNS:
        rows.append({'feature_name': feature, 'gain': float(gain_scores.get(feature, 0.0))})
    result = pd.DataFrame(rows)
    total_gain = result['gain'].sum()
    if total_gain > 0:
        result['importance'] = result['gain'] / total_gain
    else:
        result['importance'] = 0.0
    result = result.sort_values(by='importance', ascending=False).reset_index(drop=True)
    result['rank'] = np.arange(1, len(result) + 1)
    return result[['rank', 'feature_name', 'gain', 'importance']]

# Final report helpers


def build_validation_outputs(
    validation: pd.DataFrame,
    evaluation: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    predictions = validation[
        ['model_record_id', 'model_split_group_id', 'flood_label']
    ].copy()
    predictions['flood_probability'] = evaluation['validation_probabilities']
    predictions['predicted_label'] = evaluation['validation_predictions']
    predictions['correct_prediction'] = predictions['flood_label'] == predictions['predicted_label']
    matrix = confusion_matrix(
        validation['flood_label'].astype(int),
        evaluation['validation_predictions'],
        labels=[0, 1],
    )
    confusion = pd.DataFrame(
        matrix,
        index=['actual_non_flood', 'actual_flood'],
        columns=['predicted_non_flood', 'predicted_flood'],
    )
    return (predictions, confusion)


def build_metric_rows(model_name: str, evaluation: dict) -> list[dict]:
    rows = []
    for split_name in ['train', 'validation']:
        metrics = evaluation[f'{split_name}_metrics']
        row = {'model': model_name, 'split': split_name, 'threshold': DECISION_THRESHOLD, **metrics}
        rows.append(row)
    return rows

# Existing baseline comparison


def load_baseline_validation_metrics(model_name: str) -> dict | None:
    if model_name == 'random_forest':
        path = RF_BASELINE_METRICS_FILE
    elif model_name == 'xgboost':
        path = XGB_BASELINE_METRICS_FILE
    else:
        raise ValueError(f'Unknown model: {model_name}')
    if not path.exists():
        return None
    metrics = pd.read_csv(path)
    validation = metrics[metrics['split'] == 'validation']
    if validation.empty:
        return None
    row = validation.iloc[0]
    return {
        'accuracy': float(row['accuracy']),
        'precision': float(row['precision']),
        'recall': float(row['recall']),
        'f1': float(row['f1']),
        'roc_auc': float(row['roc_auc']),
        'pr_auc': float(row['pr_auc']),
    }

# Main hyperparameter tuning routine


def tune_models():
    train, validation = load_model_splits()
    group_diagnostic = build_group_diagnostic(train)
    # Random Forest randomized validation search
    rf_search = run_randomized_search(
        model_name='random_forest',
        parameter_distributions=RF_PARAMETER_DISTRIBUTIONS,
        iterations=RF_SEARCH_ITERATIONS,
        model_builder=build_random_forest,
        train=train,
        validation=validation,
        random_seed=RANDOM_SEED,
    )
    rf_evaluation = rf_search['best_evaluation']
    best_rf = rf_evaluation['model']
    # XGBoost randomized validation search
    xgb_search = run_randomized_search(
        model_name='xgboost',
        parameter_distributions=XGB_PARAMETER_DISTRIBUTIONS,
        iterations=XGB_SEARCH_ITERATIONS,
        model_builder=build_xgboost,
        train=train,
        validation=validation,
        random_seed=RANDOM_SEED + 1,
    )
    xgb_evaluation = xgb_search['best_evaluation']
    best_xgb = xgb_evaluation['model']
    # Validation outputs
    rf_predictions, rf_confusion = build_validation_outputs(validation, rf_evaluation)
    xgb_predictions, xgb_confusion = build_validation_outputs(validation, xgb_evaluation)
    # Feature importance
    rf_feature_importance = build_rf_feature_importance(best_rf)
    xgb_feature_importance = build_xgb_feature_importance(best_xgb)
    # Combined metrics
    metrics = pd.DataFrame(
        build_metric_rows('random_forest', rf_evaluation)
        + build_metric_rows('xgboost', xgb_evaluation)
    )
    # Tuned validation comparison
    comparison_rows = []
    for model_name, evaluation, search in [
        ('random_forest', rf_evaluation, rf_search),
        ('xgboost', xgb_evaluation, xgb_search),
    ]:
        validation_metrics = evaluation['validation_metrics']
        baseline = load_baseline_validation_metrics(model_name)
        row = {
            'model': model_name,
            'best_candidate_id': search['best_candidate_id'],
            'validation_accuracy': validation_metrics['accuracy'],
            'validation_precision': validation_metrics['precision'],
            'validation_recall': validation_metrics['recall'],
            'validation_f1': validation_metrics['f1'],
            'validation_roc_auc': validation_metrics['roc_auc'],
            'validation_pr_auc': validation_metrics['pr_auc'],
            'train_f1': evaluation['train_metrics']['f1'],
            'f1_generalization_gap': evaluation['generalization_gap'],
            'search_seconds': search['search_seconds'],
            'validation_inference_ms_per_row': evaluation['validation_inference_ms_per_row'],
        }
        if baseline is not None:
            row['baseline_validation_f1'] = baseline['f1']
            row['f1_change_from_baseline'] = validation_metrics['f1'] - baseline['f1']
            row['baseline_validation_roc_auc'] = baseline['roc_auc']
            row['roc_auc_change_from_baseline'] = validation_metrics['roc_auc'] - baseline['roc_auc']
            row['baseline_validation_pr_auc'] = baseline['pr_auc']
            row['pr_auc_change_from_baseline'] = validation_metrics['pr_auc'] - baseline['pr_auc']
        comparison_rows.append(row)
    comparison = pd.DataFrame(comparison_rows)
    # Output directories
    RF_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    XGB_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    TUNING_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    # Save model artifacts
    joblib.dump(best_rf, RF_MODEL_FILE)
    best_xgb.save_model(str(XGB_MODEL_FILE))
    rf_sha256 = calculate_file_sha256(RF_MODEL_FILE)
    xgb_sha256 = calculate_file_sha256(XGB_MODEL_FILE)
    # Save reports
    rf_search['results'].to_csv(RF_SEARCH_RESULTS_FILE, index=False)
    xgb_search['results'].to_csv(XGB_SEARCH_RESULTS_FILE, index=False)
    group_diagnostic.to_csv(GROUP_DIAGNOSTIC_FILE, index=False)
    metrics.to_csv(TUNED_METRICS_FILE, index=False)
    comparison.to_csv(TUNED_COMPARISON_FILE, index=False)
    rf_confusion.to_csv(RF_CONFUSION_MATRIX_FILE)
    xgb_confusion.to_csv(XGB_CONFUSION_MATRIX_FILE)
    rf_feature_importance.to_csv(RF_FEATURE_IMPORTANCE_FILE, index=False)
    xgb_feature_importance.to_csv(XGB_FEATURE_IMPORTANCE_FILE, index=False)
    rf_predictions.to_csv(RF_VALIDATION_PREDICTIONS_FILE, index=False)
    xgb_predictions.to_csv(XGB_VALIDATION_PREDICTIONS_FILE, index=False)
    # Save best parameters
    best_parameters = {
        'selection_method': 'validation_guided_randomized_search',
        'primary_selection_metric': 'validation_f1',
        'tie_break_metric_1': 'validation_roc_auc',
        'tie_break_metric_2': 'validation_pr_auc',
        'random_forest': _json_safe(rf_evaluation['parameters']),
        'xgboost': _json_safe(xgb_evaluation['parameters']),
    }
    with BEST_PARAMS_FILE.open('w', encoding='utf-8') as file:
        json.dump(best_parameters, file, indent=2)
    largest_group_rows = int(group_diagnostic['row_count'].max())
    group_count = len(group_diagnostic)
    # Metadata
    rf_metadata = {
        'model_name': 'random_forest_tuned',
        'model_type': 'RandomForestClassifier',
        'model_role': 'primary_tuned_classifier',
        'predictor_count': len(PREDICTOR_COLUMNS),
        'predictor_columns': PREDICTOR_COLUMNS,
        'training_rows': len(train),
        'validation_rows': len(validation),
        'test_rows_used': 0,
        'training_event_groups': group_count,
        'largest_training_event_group_rows': largest_group_rows,
        'cross_validation_used': False,
        'cross_validation_reason': (
            'Not used because highly uneven atomic event-group sizes produced '
            'unstable group-aware CV folds.'
        ),
        'search_method': 'validation_guided_randomized_search',
        'search_iterations': RF_SEARCH_ITERATIONS,
        'selection_metric': 'validation_f1',
        'tie_break_metrics': ['validation_roc_auc', 'validation_pr_auc'],
        'best_parameters': _json_safe(rf_evaluation['parameters']),
        'decision_threshold': DECISION_THRESHOLD,
        'class_weight': None,
        'resampling': None,
        'search_seconds': rf_search['search_seconds'],
        'random_seed': RANDOM_SEED,
        'sklearn_version': sklearn.__version__,
        'model_sha256': rf_sha256,
    }
    xgb_metadata = {
        'model_name': 'xgboost_tuned',
        'model_type': 'XGBClassifier',
        'model_role': 'comparison_tuned_classifier',
        'predictor_count': len(PREDICTOR_COLUMNS),
        'predictor_columns': PREDICTOR_COLUMNS,
        'training_rows': len(train),
        'validation_rows': len(validation),
        'test_rows_used': 0,
        'training_event_groups': group_count,
        'largest_training_event_group_rows': largest_group_rows,
        'cross_validation_used': False,
        'cross_validation_reason': (
            'Not used because highly uneven atomic event-group sizes produced '
            'unstable group-aware CV folds.'
        ),
        'search_method': 'validation_guided_randomized_search',
        'search_iterations': XGB_SEARCH_ITERATIONS,
        'selection_metric': 'validation_f1',
        'tie_break_metrics': ['validation_roc_auc', 'validation_pr_auc'],
        'best_parameters': _json_safe(xgb_evaluation['parameters']),
        'decision_threshold': DECISION_THRESHOLD,
        'scale_pos_weight': 1.0,
        'resampling': None,
        'early_stopping': False,
        'search_seconds': xgb_search['search_seconds'],
        'random_seed': RANDOM_SEED + 1,
        'xgboost_version': xgboost.__version__,
        'sklearn_version': sklearn.__version__,
        'model_sha256': xgb_sha256,
    }
    with RF_METADATA_FILE.open('w', encoding='utf-8') as file:
        json.dump(rf_metadata, file, indent=2)
    with XGB_METADATA_FILE.open('w', encoding='utf-8') as file:
        json.dump(xgb_metadata, file, indent=2)
    return {
        'train': train,
        'validation': validation,
        'group_diagnostic': group_diagnostic,
        'rf_search': rf_search,
        'xgb_search': xgb_search,
        'best_rf': best_rf,
        'best_xgb': best_xgb,
        'rf_evaluation': rf_evaluation,
        'xgb_evaluation': xgb_evaluation,
        'rf_feature_importance': rf_feature_importance,
        'xgb_feature_importance': xgb_feature_importance,
        'rf_confusion_matrix': rf_confusion,
        'xgb_confusion_matrix': xgb_confusion,
        'comparison': comparison,
    }
