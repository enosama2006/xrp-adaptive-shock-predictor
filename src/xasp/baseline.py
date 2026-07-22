"""Governed scientific baselines for first-touch event probabilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from sklearn.frozen import FrozenEstimator
except ImportError:  # scikit-learn < 1.6
    FrozenEstimator = None  # type: ignore[assignment,misc]

ALLOWED_LABELS = ("UP_10", "DOWN_10", "NO_EVENT")
EXCLUDED_LABELS = ("AMBIGUOUS", "INCOMPLETE")


@dataclass(frozen=True, slots=True)
class BaselineConfig:
    train_fraction: float = 0.70
    calibration_fraction: float = 0.15
    minimum_rows: int = 500
    random_state: int = 17
    prediction_confidence_threshold: float = 0.85
    required_empirical_precision: float = 0.85
    minimum_high_confidence_predictions: int = 50

    def __post_init__(self) -> None:
        if not 0.5 <= self.train_fraction < 0.9:
            raise ValueError("train_fraction must be in [0.5, 0.9)")
        if not 0.05 <= self.calibration_fraction < 0.3:
            raise ValueError("calibration_fraction must be in [0.05, 0.3)")
        if self.train_fraction + self.calibration_fraction >= 0.95:
            raise ValueError("at least 5% must remain for untouched test")
        if self.minimum_rows < 30:
            raise ValueError("minimum_rows is too small")
        if not 0.5 <= self.prediction_confidence_threshold < 1.0:
            raise ValueError("prediction_confidence_threshold must be in [0.5, 1)")
        if not 0.5 <= self.required_empirical_precision <= 1.0:
            raise ValueError("required_empirical_precision must be in [0.5, 1]")
        if self.minimum_high_confidence_predictions < 1:
            raise ValueError("minimum_high_confidence_predictions must be positive")


@dataclass(frozen=True, slots=True)
class BaselineReport:
    status: str
    reason: str
    row_count: int
    train_rows: int
    calibration_rows: int
    test_rows: int
    feature_names: tuple[str, ...]
    class_counts: dict[str, int]
    metrics: dict[str, Any]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")


def _temporal_split(frame: pd.DataFrame, config: BaselineConfig) -> tuple[pd.DataFrame, ...]:
    ordered = frame.sort_values("anchor_timestamp_ms", ignore_index=True)
    n_rows = len(ordered)
    train_end = int(n_rows * config.train_fraction)
    calibration_end = int(n_rows * (config.train_fraction + config.calibration_fraction))
    return ordered.iloc[:train_end], ordered.iloc[train_end:calibration_end], ordered.iloc[calibration_end:]


def _build_pipeline(config: BaselineConfig) -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=2_000,
                    class_weight="balanced",
                    random_state=config.random_state,
                ),
            ),
        ]
    )


def _calibrate_prefit_model(
    model: Pipeline,
    calibration_features: pd.DataFrame,
    calibration_labels: pd.Series,
) -> CalibratedClassifierCV:
    """Calibrate an already-fitted estimator across supported sklearn releases.

    scikit-learn 1.6 introduced ``FrozenEstimator`` for pre-fitted estimators and
    newer releases reject the legacy ``cv='prefit'`` value. Older supported
    releases still require the legacy form, so the runtime selects the API that
    is actually available instead of pinning behavior to one sklearn version.
    """

    if FrozenEstimator is not None:
        calibrated = CalibratedClassifierCV(
            estimator=FrozenEstimator(model),
            method="sigmoid",
        )
    else:  # pragma: no cover - exercised only on older sklearn releases
        calibrated = CalibratedClassifierCV(
            estimator=model,
            method="sigmoid",
            cv="prefit",
        )
    calibrated.fit(calibration_features, calibration_labels)
    return calibrated


def _wait_report(
    reason: str,
    usable: pd.DataFrame,
    feature_names: list[str],
    counts: dict[str, int],
) -> BaselineReport:
    return BaselineReport(
        "WAIT",
        reason,
        len(usable),
        0,
        0,
        0,
        tuple(feature_names),
        counts,
        {},
    )


def train_multinomial_baseline(
    dataset: pd.DataFrame,
    feature_names: list[str],
    config: BaselineConfig = BaselineConfig(),
) -> tuple[Pipeline | CalibratedClassifierCV | None, BaselineReport]:
    """Train on observed FINAL labels and fail closed unless the 85% gate passes.

    The gate means that, on an untouched temporal test period, predictions whose
    reported maximum probability is at least 85% must achieve at least 85%
    empirical precision with sufficient support. It is not a guarantee about
    future observations.
    """

    required = {"anchor_timestamp_ms", "label", "status", *feature_names}
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"baseline dataset missing columns: {sorted(missing)}")
    usable = dataset[
        (dataset["status"] == "FINAL") & dataset["label"].isin(ALLOWED_LABELS)
    ].copy().sort_values("anchor_timestamp_ms", ignore_index=True)
    counts = {label: int((usable["label"] == label).sum()) for label in ALLOWED_LABELS}
    if len(usable) < config.minimum_rows:
        return None, _wait_report("insufficient_final_rows", usable, feature_names, counts)
    if sum(value > 0 for value in counts.values()) < 2:
        return None, _wait_report("insufficient_label_diversity", usable, feature_names, counts)

    train, calibration, test = _temporal_split(usable, config)
    if train.empty or calibration.empty or test.empty:
        raise ValueError("temporal split produced an empty partition")
    model = _build_pipeline(config)
    model.fit(train[feature_names], train["label"])
    calibrated: Pipeline | CalibratedClassifierCV = model
    if calibration["label"].nunique() >= 2:
        calibrated = _calibrate_prefit_model(
            model,
            calibration[feature_names],
            calibration["label"],
        )

    probabilities = calibrated.predict_proba(test[feature_names])
    predictions = calibrated.predict(test[feature_names])
    classes = [str(value) for value in calibrated.classes_]
    precision, recall, f1, support = precision_recall_fscore_support(
        test["label"], predictions, labels=list(ALLOWED_LABELS), zero_division=0
    )
    per_class: dict[str, dict[str, float | int]] = {}
    for index, label in enumerate(ALLOWED_LABELS):
        metrics: dict[str, float | int] = {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
        if label in classes:
            class_index = classes.index(label)
            metrics["brier"] = float(
                brier_score_loss((test["label"] == label).astype(int), probabilities[:, class_index])
            )
        per_class[label] = metrics

    maximum_probability = probabilities.max(axis=1)
    high_confidence = maximum_probability >= config.prediction_confidence_threshold
    high_count = int(high_confidence.sum())
    high_precision = (
        float(np.mean(predictions[high_confidence] == test["label"].to_numpy()[high_confidence]))
        if high_count
        else 0.0
    )
    gate_passed = (
        high_count >= config.minimum_high_confidence_predictions
        and high_precision >= config.required_empirical_precision
    )
    report = BaselineReport(
        "RESEARCH_ONLY" if gate_passed else "WAIT",
        "empirical_85pct_gate_passed_not_trading_promoted"
        if gate_passed
        else "empirical_85pct_gate_failed",
        len(usable),
        len(train),
        len(calibration),
        len(test),
        tuple(feature_names),
        counts,
        {
            "per_class": per_class,
            "high_confidence_threshold": config.prediction_confidence_threshold,
            "high_confidence_predictions": high_count,
            "high_confidence_empirical_precision": high_precision,
            "required_empirical_precision": config.required_empirical_precision,
            "test_start_ms": int(test["anchor_timestamp_ms"].min()),
            "test_end_ms": int(test["anchor_timestamp_ms"].max()),
            "probability_sum_max_error": float(
                np.abs(probabilities.sum(axis=1) - 1.0).max(initial=0.0)
            ),
        },
    )
    return (calibrated if gate_passed else None), report
