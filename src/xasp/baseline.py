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
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_fscore_support,
)
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
    label_horizon_ms: int = 60_000
    embargo_ms: int | None = None
    calibration_bins: int = 10

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
        if self.label_horizon_ms <= 0:
            raise ValueError("label_horizon_ms must be positive")
        if self.embargo_ms is not None and self.embargo_ms < 0:
            raise ValueError("embargo_ms must be non-negative")
        if self.calibration_bins < 2:
            raise ValueError("calibration_bins must be at least two")


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


def _purged_temporal_split(
    frame: pd.DataFrame,
    config: BaselineConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Create chronological train/calibration/test partitions with boundary purge.

    Rows whose target horizon crosses into the next partition are removed. An
    embargo equal to the target horizon by default is applied after each boundary
    before the next partition begins. The final test remains chronologically last.
    """

    ordered = frame.sort_values("anchor_timestamp_ms", ignore_index=True).copy()
    if "horizon_end_ms" not in ordered.columns:
        ordered["horizon_end_ms"] = (
            ordered["anchor_timestamp_ms"].astype("int64") + config.label_horizon_ms
        )

    n_rows = len(ordered)
    train_cut_index = int(n_rows * config.train_fraction)
    calibration_cut_index = int(
        n_rows * (config.train_fraction + config.calibration_fraction)
    )
    if train_cut_index <= 0 or calibration_cut_index <= train_cut_index or calibration_cut_index >= n_rows:
        raise ValueError("temporal split produced an empty raw partition")

    calibration_boundary = int(ordered.iloc[train_cut_index]["anchor_timestamp_ms"])
    test_boundary = int(ordered.iloc[calibration_cut_index]["anchor_timestamp_ms"])
    embargo_ms = config.label_horizon_ms if config.embargo_ms is None else config.embargo_ms

    raw_train = ordered.iloc[:train_cut_index]
    raw_calibration = ordered.iloc[train_cut_index:calibration_cut_index]
    raw_test = ordered.iloc[calibration_cut_index:]

    train = raw_train[raw_train["horizon_end_ms"] <= calibration_boundary].copy()
    calibration = raw_calibration[
        (raw_calibration["anchor_timestamp_ms"] >= calibration_boundary + embargo_ms)
        & (raw_calibration["horizon_end_ms"] <= test_boundary)
    ].copy()
    test = raw_test[
        raw_test["anchor_timestamp_ms"] >= test_boundary + embargo_ms
    ].copy()

    audit = {
        "raw_train_rows": int(len(raw_train)),
        "raw_calibration_rows": int(len(raw_calibration)),
        "raw_test_rows": int(len(raw_test)),
        "purged_train_rows": int(len(raw_train) - len(train)),
        "purged_or_embargoed_calibration_rows": int(len(raw_calibration) - len(calibration)),
        "embargoed_test_rows": int(len(raw_test) - len(test)),
        "calibration_boundary_ms": calibration_boundary,
        "test_boundary_ms": test_boundary,
        "label_horizon_ms": config.label_horizon_ms,
        "embargo_ms": embargo_ms,
    }
    return train, calibration, test, audit


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
    """Calibrate an already-fitted estimator across supported sklearn releases."""

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


def _expected_calibration_error(
    probabilities: np.ndarray,
    predictions: np.ndarray,
    actual: np.ndarray,
    bins: int,
) -> float:
    confidence = probabilities.max(axis=1)
    correctness = (predictions == actual).astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for index in range(bins):
        lower = edges[index]
        upper = edges[index + 1]
        mask = (confidence >= lower) & (
            confidence <= upper if index == bins - 1 else confidence < upper
        )
        count = int(mask.sum())
        if count == 0:
            continue
        weight = count / len(confidence)
        ece += weight * abs(float(correctness[mask].mean()) - float(confidence[mask].mean()))
    return float(ece)


def _wait_report(
    reason: str,
    usable: pd.DataFrame,
    feature_names: list[str],
    counts: dict[str, int],
    metrics: dict[str, Any] | None = None,
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
        metrics or {},
    )


def train_multinomial_baseline(
    dataset: pd.DataFrame,
    feature_names: list[str],
    config: BaselineConfig = BaselineConfig(),
) -> tuple[Pipeline | CalibratedClassifierCV | None, BaselineReport]:
    """Train on observed FINAL labels and fail closed unless the gate passes."""

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

    train, calibration, test, split_audit = _purged_temporal_split(usable, config)
    if train.empty or calibration.empty or test.empty:
        return None, _wait_report(
            "insufficient_rows_after_purge_and_embargo",
            usable,
            feature_names,
            counts,
            {"split_audit": split_audit},
        )
    if train["label"].nunique() < 2:
        return None, _wait_report(
            "insufficient_train_label_diversity_after_purge",
            usable,
            feature_names,
            counts,
            {"split_audit": split_audit},
        )

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
    per_class: dict[str, dict[str, float | int | None]] = {}
    for index, label in enumerate(ALLOWED_LABELS):
        metrics: dict[str, float | int | None] = {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
            "pr_auc": None,
            "brier": None,
        }
        if label in classes:
            class_index = classes.index(label)
            actual_binary = (test["label"] == label).astype(int)
            metrics["brier"] = float(
                brier_score_loss(actual_binary, probabilities[:, class_index])
            )
            if actual_binary.nunique() >= 2:
                metrics["pr_auc"] = float(
                    average_precision_score(actual_binary, probabilities[:, class_index])
                )
        per_class[label] = metrics

    maximum_probability = probabilities.max(axis=1)
    high_confidence = maximum_probability >= config.prediction_confidence_threshold
    high_count = int(high_confidence.sum())
    test_actual = test["label"].to_numpy()
    high_precision = (
        float(np.mean(predictions[high_confidence] == test_actual[high_confidence]))
        if high_count
        else 0.0
    )
    gate_passed = (
        high_count >= config.minimum_high_confidence_predictions
        and high_precision >= config.required_empirical_precision
    )
    ece = _expected_calibration_error(
        probabilities,
        np.asarray(predictions),
        test_actual,
        config.calibration_bins,
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
            "split_audit": split_audit,
            "per_class": per_class,
            "expected_calibration_error": ece,
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
