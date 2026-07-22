"""Deterministic drift checks that trigger review instead of blind retraining."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss


@dataclass(frozen=True, slots=True)
class DriftThresholds:
    psi_warn: float = 0.10
    psi_critical: float = 0.25
    brier_degradation_warn: float = 0.03
    brier_degradation_critical: float = 0.07
    minimum_rows: int = 100


@dataclass(frozen=True, slots=True)
class DriftFinding:
    status: str
    reason: str
    psi_by_feature: dict[str, float]
    brier_reference: float | None
    brier_current: float | None


def population_stability_index(
    reference: pd.Series,
    current: pd.Series,
    *,
    bins: int = 10,
) -> float:
    ref = pd.to_numeric(reference, errors="coerce").dropna().to_numpy(dtype=float)
    cur = pd.to_numeric(current, errors="coerce").dropna().to_numpy(dtype=float)
    if len(ref) == 0 or len(cur) == 0:
        raise ValueError("PSI requires non-empty numeric samples")
    quantiles = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(quantiles) < 3:
        return 0.0
    quantiles[0] = -np.inf
    quantiles[-1] = np.inf
    ref_counts, _ = np.histogram(ref, bins=quantiles)
    cur_counts, _ = np.histogram(cur, bins=quantiles)
    epsilon = 1e-6
    ref_ratio = np.maximum(ref_counts / ref_counts.sum(), epsilon)
    cur_ratio = np.maximum(cur_counts / cur_counts.sum(), epsilon)
    return float(np.sum((cur_ratio - ref_ratio) * np.log(cur_ratio / ref_ratio)))


def _multiclass_brier(frame: pd.DataFrame) -> float:
    labels = ("UP_10", "DOWN_10", "NO_EVENT")
    values: list[float] = []
    for label, column in zip(labels, ("p_up_10", "p_down_10", "p_no_event"), strict=True):
        truth = (frame["actual_label"] == label).astype(int)
        values.append(float(brier_score_loss(truth, frame[column])))
    return float(np.mean(values))


def assess_drift(
    reference_features: pd.DataFrame,
    current_features: pd.DataFrame,
    feature_names: list[str],
    *,
    reference_predictions: pd.DataFrame | None = None,
    current_predictions: pd.DataFrame | None = None,
    thresholds: DriftThresholds = DriftThresholds(),
) -> DriftFinding:
    if len(reference_features) < thresholds.minimum_rows or len(current_features) < thresholds.minimum_rows:
        return DriftFinding("WAIT", "insufficient_rows", {}, None, None)
    missing = set(feature_names) - set(reference_features.columns) | set(feature_names) - set(
        current_features.columns
    )
    if missing:
        raise ValueError(f"drift frames missing features: {sorted(missing)}")

    psi = {
        name: population_stability_index(reference_features[name], current_features[name])
        for name in feature_names
    }
    maximum_psi = max(psi.values(), default=0.0)

    reference_brier: float | None = None
    current_brier: float | None = None
    degradation = 0.0
    if reference_predictions is not None and current_predictions is not None:
        required = {"actual_label", "p_up_10", "p_down_10", "p_no_event"}
        for frame in (reference_predictions, current_predictions):
            if missing_columns := required - set(frame.columns):
                raise ValueError(f"prediction drift frame missing columns: {sorted(missing_columns)}")
        reference_brier = _multiclass_brier(reference_predictions)
        current_brier = _multiclass_brier(current_predictions)
        degradation = current_brier - reference_brier

    if maximum_psi >= thresholds.psi_critical or degradation >= thresholds.brier_degradation_critical:
        return DriftFinding("CRITICAL", "quarantine_and_review", psi, reference_brier, current_brier)
    if maximum_psi >= thresholds.psi_warn or degradation >= thresholds.brier_degradation_warn:
        return DriftFinding("WARN", "review_required", psi, reference_brier, current_brier)
    return DriftFinding("PASS", "within_thresholds", psi, reference_brier, current_brier)
