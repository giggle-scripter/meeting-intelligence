"""Train and export the portable shadow action classifier."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.ml.action_features import ACTION_FEATURE_NAMES, action_feature_vector
from backend.app.ml.action_classifier import LABEL_ORDER
from backend.app.ml.embeddings import HASHING_FALLBACK_MODEL, HashingEmbeddingModel
from backend.app.ml.embeddings import SentenceTransformerEmbeddingModel


ARTIFACT_SCHEMA_VERSION = "action-classifier-artifact-v1"
TRAINER_VERSION = "action-classifier-trainer-v1"
FEATURES_VERSION = "action-features-v1"
LABEL_SCHEMA = "action-labels-v1"
DEFAULT_SEED = 20260817


def _load_dependencies() -> tuple[Any, Any, Any, Any, Any]:
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import (
            classification_report,
            confusion_matrix,
            log_loss,
            precision_recall_fscore_support,
        )
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError(
            "Training requires the optional ML dependencies. Install with "
            "`python -m pip install -e .[ml-train]`."
        ) from exc
    return (
        np,
        LogisticRegression,
        StandardScaler,
        (classification_report, confusion_matrix, log_loss),
        precision_recall_fscore_support,
    )


def _load_records(path: Path) -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    eligible = [record for record in records if record.get("eligible_for_training")]
    if not eligible:
        raise ValueError("training dataset contains no eligible records")
    if any(not record.get("label") for record in eligible):
        raise ValueError("eligible training records must have labels")
    return eligible


def _matrix(records: list[dict[str, Any]], embedding_model: Any, np: Any) -> tuple[Any, Any]:
    embeddings = embedding_model.embed([record["semantic_text"] for record in records])
    rows = [
        embedding + action_feature_vector(record["features"])
        for embedding, record in zip(embeddings, records, strict=True)
    ]
    return np.asarray(rows, dtype=float), np.asarray(
        [record["label"] for record in records], dtype=object
    )


def _fit(X: Any, y: Any, seed: int, LogisticRegression: Any, StandardScaler: Any) -> tuple[Any, Any]:
    scaler = StandardScaler()
    transformed = scaler.fit_transform(X)
    model = LogisticRegression(
        class_weight="balanced",
        max_iter=500,
        random_state=seed,
        solver="saga",
        tol=1e-3,
    )
    model.fit(transformed, y)
    return scaler, model


def _fold_metrics(
    y_true: Any,
    y_pred: Any,
    probabilities: Any,
    classes: list[str],
    metric_functions: tuple[Any, Any, Any],
    binary_metric: Any,
) -> dict[str, Any]:
    classification_report, confusion_matrix, log_loss = metric_functions
    labels = [label.value for label in LABEL_ORDER]
    report = classification_report(
        y_true,
        y_pred,
        labels=labels,
        output_dict=True,
        zero_division=0,
    )
    action_labels = {"CLEAR_ACTION", "POSSIBLE_ACTION"}
    true_action = [value in action_labels for value in y_true]
    predicted_action = [value in action_labels for value in y_pred]
    precision, recall, f1, _ = binary_metric(
        true_action,
        predicted_action,
        average="binary",
        zero_division=0,
    )
    return {
        "accuracy": float(report["accuracy"]),
        "macro_f1": float(report["macro avg"]["f1-score"]),
        "weighted_f1": float(report["weighted avg"]["f1-score"]),
        "log_loss": float(log_loss(y_true, probabilities, labels=classes)),
        "binary_action": {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
        },
        "per_label": {
            label: {
                "precision": float(report[label]["precision"]),
                "recall": float(report[label]["recall"]),
                "f1": float(report[label]["f1-score"]),
                "support": int(report[label]["support"]),
            }
            for label in labels
        },
        "confusion_matrix": confusion_matrix(
            y_true, y_pred, labels=labels
        ).astype(int).tolist(),
        "confusion_matrix_labels": labels,
    }


def train(
    dataset_path: Path,
    folds_path: Path,
    dataset_report_path: Path,
    *,
    embedding_model_name: str,
    embedding_device: str,
    dimension: int,
    seed: int,
    model_name: str,
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    np, LogisticRegression, StandardScaler, metric_functions, binary_metric = (
        _load_dependencies()
    )
    records = _load_records(dataset_path)
    folds_payload = json.loads(folds_path.read_text(encoding="utf-8"))
    dataset_report = json.loads(dataset_report_path.read_text(encoding="utf-8"))
    embedding_model = (
        HashingEmbeddingModel(dimension)
        if embedding_model_name == HASHING_FALLBACK_MODEL
        else SentenceTransformerEmbeddingModel(
            embedding_model_name, device=embedding_device
        )
    )
    embedding_metadata = embedding_model.metadata
    X, y = _matrix(records, embedding_model, np)
    meeting_ids = np.asarray([record["meeting_id"] for record in records], dtype=object)
    cross_validation: list[dict[str, Any]] = []
    for fold in folds_payload["folds"]:
        validation_ids = set(fold["validation_meeting_ids"])
        validation_mask = np.asarray(
            [meeting_id in validation_ids for meeting_id in meeting_ids], dtype=bool
        )
        train_mask = ~validation_mask
        scaler, model = _fit(
            X[train_mask], y[train_mask], seed, LogisticRegression, StandardScaler
        )
        validation_X = scaler.transform(X[validation_mask])
        predictions = model.predict(validation_X)
        probabilities = model.predict_proba(validation_X)
        cross_validation.append(
            {
                "fold": int(fold["fold"]),
                "train_record_count": int(train_mask.sum()),
                "validation_record_count": int(validation_mask.sum()),
                "iterations": int(model.n_iter_.max()),
                **_fold_metrics(
                    y[validation_mask],
                    predictions,
                    probabilities,
                    model.classes_.tolist(),
                    metric_functions,
                    binary_metric,
                ),
            }
        )

    scaler, model = _fit(X, y, seed, LogisticRegression, StandardScaler)
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "manifest": {
            "model_name": model_name,
            "embedding_model": embedding_metadata.model_name,
            "embedding_model_version": embedding_metadata.model_version,
            "embedding_backend": embedding_metadata.backend,
            "training_dataset_version": dataset_report["dataset_version"],
            "created_at": created_at,
            "features_version": FEATURES_VERSION,
            "label_schema": LABEL_SCHEMA,
        },
        "linear_model": {
            "classes": model.classes_.tolist(),
            "feature_names": list(ACTION_FEATURE_NAMES),
            "embedding_dimension": embedding_metadata.dimension,
            "scaler_mean": scaler.mean_.astype(float).tolist(),
            "scaler_scale": scaler.scale_.astype(float).tolist(),
            "coefficients": model.coef_.astype(float).tolist(),
            "intercepts": model.intercept_.astype(float).tolist(),
        },
    }
    macro_f1 = [item["macro_f1"] for item in cross_validation]
    action_f1 = [item["binary_action"]["f1"] for item in cross_validation]
    report = {
        "trainer_version": TRAINER_VERSION,
        "model_name": model_name,
        "created_at": created_at,
        "training_dataset_version": dataset_report["dataset_version"],
        "record_count": len(records),
        "meeting_count": len(set(meeting_ids.tolist())),
        "label_counts": dict(sorted(Counter(y.tolist()).items())),
        "training_configuration": {
            "estimator": "sklearn.linear_model.LogisticRegression",
            "solver": "saga",
            "class_weight": "balanced",
            "max_iter": 500,
            "tolerance": 0.001,
            "seed": seed,
        },
        "embedding": {
            "model": embedding_metadata.model_name,
            "version": embedding_metadata.model_version,
            "backend": embedding_metadata.backend,
            "dimension": embedding_metadata.dimension,
            "device": embedding_metadata.device,
            "semantic": embedding_metadata.backend == "sentence-transformers",
        },
        "cross_validation": cross_validation,
        "summary": {
            "mean_macro_f1": float(sum(macro_f1) / len(macro_f1)),
            "mean_binary_action_f1": float(sum(action_f1) / len(action_f1)),
            "production_threshold_tuning_allowed": bool(
                dataset_report["training_readiness"][
                    "production_threshold_tuning_allowed"
                ]
            ),
        },
    }
    return artifact, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/ml/action-classifier/train.jsonl"),
    )
    parser.add_argument(
        "--folds",
        type=Path,
        default=Path("data/ml/action-classifier/folds.json"),
    )
    parser.add_argument(
        "--dataset-report",
        type=Path,
        default=Path("data/ml/action-classifier/dataset-report.json"),
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=Path("data/ml/action-classifier/model/action-clf-v1.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/ml/action-classifier/model/training-report.json"),
    )
    parser.add_argument("--dimension", type=int, default=384)
    parser.add_argument(
        "--embedding-model",
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    parser.add_argument("--embedding-device", default="cpu")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--model-name", default="action-clf-v1")
    parser.add_argument(
        "--created-at",
        default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    args = parser.parse_args()
    if args.dimension <= 0:
        parser.error("--dimension must be greater than zero")
    artifact, report = train(
        args.dataset,
        args.folds,
        args.dataset_report,
        embedding_model_name=args.embedding_model,
        embedding_device=args.embedding_device,
        dimension=args.dimension,
        seed=args.seed,
        model_name=args.model_name,
        created_at=args.created_at,
    )
    for path, payload in ((args.artifact, artifact), (args.report, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"Artifact: {args.artifact}")
    print(f"Report: {args.report}")
    print(
        "CV mean macro F1={:.4f}; binary action F1={:.4f}".format(
            report["summary"]["mean_macro_f1"],
            report["summary"]["mean_binary_action_f1"],
        )
    )


if __name__ == "__main__":
    main()
