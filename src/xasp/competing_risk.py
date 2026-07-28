"""Joint competing-risk model for the governed eight-hour first-touch question.

The historical implementation fitted one classifier per cumulative horizon.
Those classifiers could emit mutually inconsistent cumulative probabilities.
This module fits one categorical event-time model instead:

``UP_H1 .. UP_H8, DOWN_H1 .. DOWN_H8, NO_EVENT``.

Every hourly cumulative probability is derived from that single distribution,
so event probability can only increase with time and no-touch probability can
only decrease.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .horizons import RESEARCH_HORIZONS_MINUTES
from .target_definition import TARGET_DEFINITION_VERSION

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
MAX_HORIZON_MINUTES = max(RESEARCH_HORIZONS_MINUTES)
COMPETING_RISK_MODEL_KIND = "joint_hourly_competing_risk"
COMPETING_RISK_GATE_VERSION = "joint-2pct-hourly-competing-risk-v1"
NO_EVENT_CLASS = "NO_EVENT"
ALL_EVENT_CLASSES = sorted(
    [
        *(f"UP_H{hour}" for hour in range(1, 9)),
        *(f"DOWN_H{hour}" for hour in range(1, 9)),
        NO_EVENT_CLASS,
    ]
)

try:
    from sklearn.frozen import FrozenEstimator
except ImportError:  # pragma: no cover - supported sklearn fallback
    FrozenEstimator = None


@dataclass(frozen=True, slots=True)
class CompetingRiskConfig:
    minimum_rows: int = 2_000
    train_fraction: float = 0.70
    calibration_fraction: float = 0.15
    random_state: int = 17
    required_directional_precision: float = 0.85
    minimum_directional_signals: int = 30
    minimum_signals_per_direction: int = 5
    event_probability_candidates: tuple[float, ...] = (
        0.20,
        0.30,
        0.40,
        0.50,
        0.60,
        0.70,
    )
    confidence_candidates: tuple[float, ...] = (
        0.55,
        0.60,
        0.65,
        0.70,
        0.75,
        0.80,
        0.85,
    )

    def __post_init__(self) -> None:
        if self.minimum_rows < 30:
            raise ValueError("minimum_rows is too small")
        if not 0.5 <= self.train_fraction < 0.9:
            raise ValueError("train_fraction must be in [0.5, 0.9)")
        if not 0.05 <= self.calibration_fraction < 0.3:
            raise ValueError("calibration_fraction must be in [0.05, 0.3)")
        if self.train_fraction + self.calibration_fraction >= 0.95:
            raise ValueError("at least 5% must remain for untouched test")
        if not 0.5 <= self.required_directional_precision <= 1.0:
            raise ValueError("required_directional_precision must be in [0.5, 1]")
        if self.minimum_directional_signals < 1:
            raise ValueError("minimum_directional_signals must be positive")
        if self.minimum_signals_per_direction < 1:
            raise ValueError("minimum_signals_per_direction must be positive")
        if not self.event_probability_candidates:
            raise ValueError("event_probability_candidates cannot be empty")
        if any(
            not 0.0 <= threshold < 1.0
            for threshold in self.event_probability_candidates
        ):
            raise ValueError("event-probability candidates must be in [0, 1)")
        if not self.confidence_candidates:
            raise ValueError("confidence_candidates cannot be empty")


@dataclass(frozen=True, slots=True)
class CompetingRiskReport:
    status: str
    reason: str
    row_count: int
    train_rows: int
    calibration_rows: int
    test_rows: int
    class_counts: dict[str, int]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _event_class(label: str, anchor_ms: int, touch_ms: object) -> str | None:
    if label == NO_EVENT_CLASS:
        return NO_EVENT_CLASS
    if label not in {"UP_02", "DOWN_02"} or touch_ms is None or pd.isna(touch_ms):
        return None
    try:
        normalized_touch_ms = int(float(str(touch_ms)))
    except (TypeError, ValueError):
        return None
    delta = normalized_touch_ms - anchor_ms
    if delta <= 0 or delta > MAX_HORIZON_MINUTES * MINUTE_MS:
        return None
    hour = int(math.ceil(delta / HOUR_MS))
    direction = "UP" if label == "UP_02" else "DOWN"
    return f"{direction}_H{hour}"


def build_competing_risk_dataset(
    horizon_rows: pd.DataFrame,
    feature_names: list[str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Build one event-time target row per fully observed eight-hour anchor."""

    required = {
        "anchor_timestamp_ms",
        "horizon_minutes",
        "horizon_end_ms",
        "label",
        "status",
        "touch_timestamp_ms",
        *feature_names,
    }
    missing = required - set(horizon_rows.columns)
    if missing:
        raise ValueError(f"competing-risk dataset missing columns: {sorted(missing)}")

    selected = horizon_rows[
        horizon_rows["horizon_minutes"] == MAX_HORIZON_MINUTES
    ].copy()
    audit = {
        "input_rows": int(len(horizon_rows)),
        "maximum_horizon_rows": int(len(selected)),
        "excluded_non_final": 0,
        "excluded_invalid_event_time": 0,
        "usable_rows": 0,
    }
    if selected.empty:
        selected["event_class"] = pd.Series(dtype="object")
        return selected, audit

    final = selected[
        (selected["status"] == "FINAL")
        & selected["label"].isin(("UP_02", "DOWN_02", NO_EVENT_CLASS))
    ].copy()
    audit["excluded_non_final"] = int(len(selected) - len(final))
    event_classes = [
        _event_class(
            str(row.label),
            int(row.anchor_timestamp_ms),
            row.touch_timestamp_ms,
        )
        for row in final.itertuples(index=False)
    ]
    final["event_class"] = event_classes
    invalid = final["event_class"].isna()
    audit["excluded_invalid_event_time"] = int(invalid.sum())
    final = final[~invalid].copy()
    final = final.drop_duplicates("anchor_timestamp_ms", keep="last")
    final = final.sort_values("anchor_timestamp_ms", ignore_index=True)
    audit["usable_rows"] = int(len(final))
    return final, audit


def _purged_split(
    frame: pd.DataFrame,
    config: CompetingRiskConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    ordered = frame.sort_values("anchor_timestamp_ms", ignore_index=True)
    count = len(ordered)
    train_cut = int(count * config.train_fraction)
    calibration_cut = int(
        count * (config.train_fraction + config.calibration_fraction)
    )
    if train_cut <= 0 or calibration_cut <= train_cut or calibration_cut >= count:
        raise ValueError("temporal split produced an empty raw partition")

    calibration_boundary = int(ordered.iloc[train_cut]["anchor_timestamp_ms"])
    test_boundary = int(ordered.iloc[calibration_cut]["anchor_timestamp_ms"])
    horizon_ms = MAX_HORIZON_MINUTES * MINUTE_MS
    raw_train = ordered.iloc[:train_cut]
    raw_calibration = ordered.iloc[train_cut:calibration_cut]
    raw_test = ordered.iloc[calibration_cut:]
    train = raw_train[raw_train["horizon_end_ms"] <= calibration_boundary].copy()
    calibration = raw_calibration[
        (raw_calibration["anchor_timestamp_ms"] >= calibration_boundary + horizon_ms)
        & (raw_calibration["horizon_end_ms"] <= test_boundary)
    ].copy()
    test = raw_test[
        raw_test["anchor_timestamp_ms"] >= test_boundary + horizon_ms
    ].copy()
    audit = {
        "raw_train_rows": int(len(raw_train)),
        "raw_calibration_rows": int(len(raw_calibration)),
        "raw_test_rows": int(len(raw_test)),
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "test_rows": int(len(test)),
        "purged_train_rows": int(len(raw_train) - len(train)),
        "purged_or_embargoed_calibration_rows": int(
            len(raw_calibration) - len(calibration)
        ),
        "embargoed_test_rows": int(len(raw_test) - len(test)),
        "horizon_ms": horizon_ms,
    }
    return train, calibration, test, audit


def _pipeline(config: CompetingRiskConfig) -> Pipeline:
    return Pipeline(
        [
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


def _calibrate(
    fitted: Pipeline,
    features: pd.DataFrame,
    labels: pd.Series,
) -> Pipeline | CalibratedClassifierCV:
    if set(str(value) for value in labels.unique()) != {
        str(value) for value in fitted.classes_
    }:
        return fitted
    if FrozenEstimator is not None:
        calibrated = CalibratedClassifierCV(
            estimator=FrozenEstimator(fitted),
            method="sigmoid",
        )
    else:  # pragma: no cover
        calibrated = CalibratedClassifierCV(
            estimator=fitted,
            method="sigmoid",
            cv="prefit",
        )
    calibrated.fit(features, labels)
    return calibrated


def _directional_arrays(
    probabilities: np.ndarray,
    classes: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    up = np.zeros(len(probabilities), dtype=float)
    down = np.zeros(len(probabilities), dtype=float)
    no_event = np.zeros(len(probabilities), dtype=float)
    for index, label in enumerate(classes):
        if label.startswith("UP_H"):
            up += probabilities[:, index]
        elif label.startswith("DOWN_H"):
            down += probabilities[:, index]
        elif label == NO_EVENT_CLASS:
            no_event += probabilities[:, index]
    return up, down, no_event


def _policy_metrics(
    actual_classes: np.ndarray,
    probabilities: np.ndarray,
    classes: list[str],
    *,
    minimum_event_probability: float,
    minimum_direction_confidence: float,
) -> dict[str, Any]:
    up, down, _ = _directional_arrays(probabilities, classes)
    event = up + down
    confidence = np.divide(
        np.maximum(up, down),
        event,
        out=np.full_like(event, 0.5),
        where=event > 0,
    )
    signals = (event >= minimum_event_probability) & (
        confidence >= minimum_direction_confidence
    )
    predicted_up = up >= down
    actual = np.asarray(
        [
            "UP" if str(value).startswith("UP_H") else
            "DOWN" if str(value).startswith("DOWN_H") else
            "NO_EVENT"
            for value in actual_classes
        ]
    )
    predicted = np.where(predicted_up, "UP", "DOWN")
    support = int(signals.sum())
    correct = int(((predicted == actual) & signals).sum())
    up_support = int((signals & (predicted == "UP")).sum())
    down_support = int((signals & (predicted == "DOWN")).sum())
    return {
        "minimum_event_probability": minimum_event_probability,
        "minimum_direction_confidence": minimum_direction_confidence,
        "signal_rows": support,
        "predicted_up_rows": up_support,
        "predicted_down_rows": down_support,
        "precision": None if support == 0 else correct / support,
    }


def _select_policy(
    actual_classes: np.ndarray,
    probabilities: np.ndarray,
    classes: list[str],
    config: CompetingRiskConfig,
) -> dict[str, Any]:
    candidates = [
        _policy_metrics(
            actual_classes,
            probabilities,
            classes,
            minimum_event_probability=event_threshold,
            minimum_direction_confidence=direction_threshold,
        )
        for event_threshold in config.event_probability_candidates
        for direction_threshold in config.confidence_candidates
    ]
    passing = [
        item
        for item in candidates
        if item["signal_rows"] >= config.minimum_directional_signals
        and item["predicted_up_rows"] >= config.minimum_signals_per_direction
        and item["predicted_down_rows"] >= config.minimum_signals_per_direction
        and item["precision"] is not None
        and float(item["precision"]) >= config.required_directional_precision
    ]
    selected = max(
        passing,
        key=lambda item: (
            int(item["signal_rows"]),
            float(item["precision"]),
            -float(item["minimum_direction_confidence"]),
        ),
        default=None,
    )
    return {
        "status": "PASS" if selected is not None else "WAIT",
        "reason": (
            "validation_selected_empirical_direction_policy"
            if selected is not None
            else "no_validation_threshold_met_directional_support_and_precision"
        ),
        "required_directional_precision": config.required_directional_precision,
        "minimum_directional_signals": config.minimum_directional_signals,
        "minimum_signals_per_direction": config.minimum_signals_per_direction,
        "selected": selected,
        "candidates": candidates,
    }


def _probability_metrics(
    actual: pd.Series,
    probabilities: np.ndarray,
    classes: list[str],
    train: pd.Series,
) -> dict[str, Any]:
    epsilon = 1e-12
    evaluation_classes = list(ALL_EVENT_CLASSES)
    evaluation_index = {
        label: index for index, label in enumerate(evaluation_classes)
    }
    expanded_probabilities = np.zeros(
        (len(probabilities), len(evaluation_classes)),
        dtype=float,
    )
    for source_index, label in enumerate(classes):
        target_index = evaluation_index.get(label)
        if target_index is not None:
            expanded_probabilities[:, target_index] = probabilities[:, source_index]
    expanded_probabilities = np.clip(expanded_probabilities, epsilon, 1.0)
    expanded_probabilities /= expanded_probabilities.sum(axis=1, keepdims=True)
    encoded = np.zeros_like(expanded_probabilities)
    for row_index, label in enumerate(actual.astype(str)):
        index = evaluation_index.get(label)
        if index is not None:
            encoded[row_index, index] = 1.0
    train_rates = train.astype(str).value_counts(normalize=True)
    baseline = np.asarray(
        [
            max(epsilon, float(train_rates.get(label, 0.0)))
            for label in evaluation_classes
        ],
        dtype=float,
    )
    baseline /= baseline.sum()
    baseline_probabilities = np.tile(baseline, (len(actual), 1))
    return {
        "multiclass_log_loss": float(
            log_loss(
                actual,
                expanded_probabilities,
                labels=evaluation_classes,
            )
        ),
        "baseline_prior_log_loss": float(
            log_loss(
                actual,
                baseline_probabilities,
                labels=evaluation_classes,
            )
        ),
        "multiclass_brier": float(
            np.mean(np.sum((expanded_probabilities - encoded) ** 2, axis=1))
        ),
        "probability_sum_max_error": float(
            np.max(np.abs(probabilities.sum(axis=1) - 1.0), initial=0.0)
        ),
        "evaluation_classes": evaluation_classes,
        "classes_absent_from_training": sorted(
            set(evaluation_classes) - set(classes)
        ),
    }


def train_competing_risk_model(
    horizon_rows: pd.DataFrame,
    feature_names: list[str],
    config: CompetingRiskConfig = CompetingRiskConfig(),
) -> tuple[Pipeline | CalibratedClassifierCV | None, CompetingRiskReport]:
    """Train one coherent event-direction/time model on eight-hour outcomes."""

    usable, target_audit = build_competing_risk_dataset(horizon_rows, feature_names)
    counts = {
        str(key): int(value)
        for key, value in usable.get("event_class", pd.Series(dtype="object"))
        .value_counts()
        .sort_index()
        .items()
    }
    common_metrics = {
        "gate_methodology_version": COMPETING_RISK_GATE_VERSION,
        "model_kind": COMPETING_RISK_MODEL_KIND,
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "target_audit": target_audit,
    }
    if len(usable) < config.minimum_rows:
        return None, CompetingRiskReport(
            "WAIT",
            "insufficient_complete_eight_hour_rows",
            len(usable),
            0,
            0,
            0,
            counts,
            {**common_metrics, "minimum_rows": config.minimum_rows},
        )
    directions = {
        "UP" if value.startswith("UP_H") else
        "DOWN" if value.startswith("DOWN_H") else
        value
        for value in counts
    }
    if not {"UP", "DOWN"}.issubset(directions):
        return None, CompetingRiskReport(
            "WAIT",
            "both_directional_event_types_are_required",
            len(usable),
            0,
            0,
            0,
            counts,
            common_metrics,
        )

    train, calibration, test, split_audit = _purged_split(usable, config)
    if min(len(train), len(calibration), len(test)) == 0:
        return None, CompetingRiskReport(
            "WAIT",
            "insufficient_rows_after_eight_hour_purge_and_embargo",
            len(usable),
            len(train),
            len(calibration),
            len(test),
            counts,
            {**common_metrics, "split_audit": split_audit},
        )
    if train["event_class"].nunique() < 2:
        return None, CompetingRiskReport(
            "WAIT",
            "insufficient_training_class_diversity",
            len(usable),
            len(train),
            len(calibration),
            len(test),
            counts,
            {**common_metrics, "split_audit": split_audit},
        )

    base = _pipeline(config)
    base.fit(train[feature_names], train["event_class"])
    model = _calibrate(
        base,
        calibration[feature_names],
        calibration["event_class"],
    )
    classes = [str(value) for value in model.classes_]
    calibration_probabilities = model.predict_proba(calibration[feature_names])
    test_probabilities = model.predict_proba(test[feature_names])
    probability_metrics = _probability_metrics(
        test["event_class"],
        test_probabilities,
        classes,
        train["event_class"],
    )
    validation_policy = _select_policy(
        calibration["event_class"].to_numpy(),
        calibration_probabilities,
        classes,
        config,
    )
    selected = validation_policy.get("selected")
    untouched_policy: dict[str, Any] | None = None
    advisory_passed = False
    if isinstance(selected, dict):
        untouched_policy = _policy_metrics(
            test["event_class"].to_numpy(),
            test_probabilities,
            classes,
            minimum_event_probability=float(selected["minimum_event_probability"]),
            minimum_direction_confidence=float(
                selected["minimum_direction_confidence"]
            ),
        )
        advisory_passed = (
            untouched_policy["signal_rows"] >= config.minimum_directional_signals
            and untouched_policy["predicted_up_rows"]
            >= config.minimum_signals_per_direction
            and untouched_policy["predicted_down_rows"]
            >= config.minimum_signals_per_direction
            and untouched_policy["precision"] is not None
            and float(untouched_policy["precision"])
            >= config.required_directional_precision
        )

    beats_prior = (
        probability_metrics["multiclass_log_loss"]
        < probability_metrics["baseline_prior_log_loss"]
    )
    report_status = "RESEARCH_READY" if beats_prior else "FORECAST_ONLY"
    report_reason = (
        "joint_probability_model_beats_train_prior_baseline"
        if beats_prior
        else "joint_probability_model_available_but_not_better_than_prior_baseline"
    )
    metrics = {
        **common_metrics,
        "split_audit": split_audit,
        "classes": classes,
        "calibrated": isinstance(model, CalibratedClassifierCV),
        "probability_metrics": probability_metrics,
        "validation_policy_selection": validation_policy,
        "advisory_gate": {
            "status": "PASS" if advisory_passed else "WAIT",
            "reason": (
                "validation_policy_passed_untouched_directional_test"
                if advisory_passed
                else "validation_policy_failed_or_was_unavailable_on_untouched_test"
            ),
            "selected_policy": selected,
            "untouched_test": untouched_policy,
        },
    }
    return model, CompetingRiskReport(
        report_status,
        report_reason,
        len(usable),
        len(train),
        len(calibration),
        len(test),
        counts,
        metrics,
    )


def predict_hourly_competing_risks(
    model: Pipeline | CalibratedClassifierCV,
    row: pd.DataFrame,
) -> list[dict[str, float | int | str]]:
    """Project one event-time distribution into coherent hourly probabilities."""

    raw = np.asarray(model.predict_proba(row)[0], dtype=float)
    raw = np.clip(raw, 0.0, 1.0)
    total = float(raw.sum())
    if total <= 0:
        raise ValueError("competing-risk model returned no probability mass")
    raw /= total
    classes = [str(value) for value in model.classes_]
    mass = {label: float(raw[index]) for index, label in enumerate(classes)}
    output: list[dict[str, float | int | str]] = []
    cumulative_up = 0.0
    cumulative_down = 0.0
    for horizon in RESEARCH_HORIZONS_MINUTES:
        hour = horizon // 60
        up_increment = mass.get(f"UP_H{hour}", 0.0)
        down_increment = mass.get(f"DOWN_H{hour}", 0.0)
        cumulative_up += up_increment
        cumulative_down += down_increment
        no_touch = max(0.0, 1.0 - cumulative_up - cumulative_down)
        event = cumulative_up + cumulative_down
        conditional_up = cumulative_up / event if event else 0.5
        conditional_down = cumulative_down / event if event else 0.5
        output.append(
            {
                "horizon_minutes": horizon,
                "hour": hour,
                "p_up_02": cumulative_up,
                "p_down_02": cumulative_down,
                "p_no_event": no_touch,
                "p_up_in_hour": up_increment,
                "p_down_in_hour": down_increment,
                "event_probability": event,
                "conditional_up_given_touch": conditional_up,
                "conditional_down_given_touch": conditional_down,
                "directional_bias": (
                    "LONG" if conditional_up > conditional_down else
                    "SHORT" if conditional_down > conditional_up else
                    "NEUTRAL"
                ),
            }
        )
    return output


__all__ = [
    "COMPETING_RISK_GATE_VERSION",
    "COMPETING_RISK_MODEL_KIND",
    "CompetingRiskConfig",
    "CompetingRiskReport",
    "build_competing_risk_dataset",
    "predict_hourly_competing_risks",
    "train_competing_risk_model",
]
