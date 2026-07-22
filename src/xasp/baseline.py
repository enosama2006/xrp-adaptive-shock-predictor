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

ALLOWED_LABELS = ("UP_10", "DOWN_10", "NO_EVENT")
EXCLUDED_LABELS = ("AMBIGUOUS", "INCOMPLETE")


@dataclass(frozen=True, slots=True)
class BaselineConfig:
    train_fraction: float = 0.70
    calibration_fraction: float = 0.15
    minimum_rows: int = 500
    random_state: int = 17

    def __post_init__(self) -> None:
        if not 0.5 <= self.train_fraction < 0.9:
            raise ValueError("train_fraction must be in [0.5, 0.9)")
        if not 0.05 <= self.calibration_fraction < 0.3:
            raise ValueError("calibration_fraction must be in [0.05, 0.3)")
        if self.train_fraction + self.calibration_fraction >= 0.95:
            raise ValueError("at least 5% must remain for untouched test")
        if self.minimum_rows < 30:
            raise ValueError("minimum_rows is too small")


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
    classifier = LogisticRegression(
        max_iter=2_000,
        class_weight="balanced",
        random_state=config.random_state,
        multi_class="auto",
    )
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scaler", StandardScaler()),
            ("classifier", classifier),
        ]
    )


def train_multinomial_baseline(
    dataset: pd.DataFrame,
    feature_names: list[str],
    config: BaselineConfig = BaselineConfig(),
) -> tuple[Pipeline | CalibratedClassifierCV | None, BaselineReport]:
    """Train a temporal multinomial baseline and report untouched-test metrics.

    Rows outside FINAL and the three supported labels are excluded. The model
    remains unavailable when evidence is insufficient; callers must interpret
    that as WAIT rather than fabricate probabilities.
    """

    required = {"anchor_timestamp_ms", "label", "status", *feature_names}
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"baseline dataset missing columns: {sorted(missing)}")

    usable = dataset[
        (dataset["status"] == "FINAL") & dataset["label"].isin(ALLOWED_LABELS)
    ].copy()
    usable = usable.sort_values("anchor_timestamp_ms", ignore_index=True)
    counts = {label: int((usable["label"] == label).sum()) for label in ALLOWED_LABELS}

    if len(usable) < config.minimum_rows:
        return None, BaselineReport(
            status="WAIT",
            reason="insufficient_final_rows",
            row_count=len(usable),
            train_rows=0,
            calibration_rows=0,
            test_rows=0,
            feature_names=tuple(feature_names),
            class_counts=counts,
            metrics={},
        )
    if sum(value > 0 for value in counts.values()) < 2:
        return None, BaselineReport(
            status="WAIT",
            reason="insufficient_label_diversity",
            row_count=len(usable),
            train_rows=0,
            calibration_rows=0,
            test_rows=0,
            feature_names=tuple(feature_names),
            class_counts=counts,
            metrics={},
        )

    train, calibration, test = _temporal_split(usable, config)
    if train.empty or calibration.empty or test.empty:
        raise ValueError("temporal split produced an empty partition")

    model = _build_pipeline(config)
    model.fit(train[feature_names], train["label"])

    calibrated: Pipeline | CalibratedClassifierCV = model
    if calibration["label"].nunique() >= 2:
        calibrated = CalibratedClassifierCV(model, method="sigmoid", cv="prefit")
        calibrated.fit(calibration[feature_names], calibration["label"])

    probabilities = calibrated.predict_proba(test[feature_names])
    predictions = calibrated.predict(test[feature_names])
    classes = [str(value) for value in calibrated.classes_]

    precision, recall, f1, support = precision_recall_fscore_support(
        test["label"],
        predictions,
        labels=list(ALLOWED_LABELS),
        zero_division=0,
    )
    per_class: dict[str, dict[str, float | int]] = {}
    for index, label in enumerate(ALLOWED_LABELS):
        class_metrics: dict[str, float | int] = {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
        if label in classes:
            class_index = classes.index(label)
            binary_truth = (test["label"] == label).astype(int)
            class_metrics["brier"] = float(
                brier_score_loss(binary_truth, probabilities[:, class_index])
            )
        per_class[label] = class_metrics

    report = BaselineReport(
        status="RESEARCH_ONLY",
        reason="baseline_trained_not_promoted",
        row_count=len(usable),
        train_rows=len(train),
        calibration_rows=len(calibration),
        test_rows=len(test),
        feature_names=tuple(feature_names),
        class_counts=counts,
        metrics={
            "per_class": per_class,
            "test_start_ms": int(test["anchor_timestamp_ms"].min()),
            "test_end_ms": int(test["anchor_timestamp_ms"].max()),
            "probability_sum_max_error": float(
                np.abs(probabilities.sum(axis=1) - 1.0).max(initial=0.0)
            ),
        },
    )
    return calibrated, report
