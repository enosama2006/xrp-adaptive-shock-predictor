"""Read-only model laboratory backed by the active governed bundles.

The laboratory explains model state, training evidence, feature distributions, and
allows non-persistent what-if inference. It never writes to the prediction ledger,
changes a champion, or promotes a model for trading.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from .competing_risk import (
    COMPETING_RISK_GATE_VERSION,
    predict_hourly_competing_risks,
)
from .feature_registry import SCHEMA_VERSION as FEATURE_SCHEMA_VERSION
from .feature_registry import audit_feature_columns
from .future_envelope import predict_envelope
from .horizons import RESEARCH_HORIZON_SET_VERSION, RESEARCH_HORIZONS_MINUTES
from .platform_runtime_v2 import RealDataPlatformV2
from .target_definition import (
    TARGET_DEFINITION_VERSION,
    TARGET_DOWN_RETURN,
    TARGET_PERCENT,
    TARGET_UP_RETURN,
)

MODEL_A_KEY = "adaptive_shock"
MODEL_B_KEY = "first_touch"
MODEL_KEYS = (MODEL_A_KEY, MODEL_B_KEY)


def _json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "WAIT", "reason": f"missing_report:{path.name}"}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return {"status": "WAIT", "reason": f"invalid_report:{path.name}"}
    return cast(dict[str, Any], value)


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _bundle_horizons(bundle: dict[str, Any] | None) -> list[int]:
    if bundle is None:
        return []
    if bundle.get("joint_model") is not None:
        return sorted(int(value) for value in bundle.get("available_horizons", ()))
    raw = bundle.get("models", {})
    if not isinstance(raw, dict):
        return []
    return sorted(int(value) for value in raw)


def _model_for_horizon(bundle: dict[str, Any], horizon: int) -> Any | None:
    raw = bundle.get("models", {})
    if not isinstance(raw, dict):
        return None
    if horizon in raw:
        return raw[horizon]
    return raw.get(str(horizon))


def _feature_names(bundle: dict[str, Any] | None) -> list[str]:
    if bundle is None:
        return []
    raw = bundle.get("feature_names", [])
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(value) for value in raw]


def _model_a_algorithm() -> dict[str, Any]:
    return {
        "family": "Gradient-boosted decision trees for quantile regression",
        "estimator": "sklearn.ensemble.HistGradientBoostingRegressor",
        "targets": [
            "future_max_return",
            "future_min_return",
            "future_close_return",
        ],
        "governed_upside_objective": TARGET_UP_RETURN,
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "quantiles": [0.05, 0.50, 0.95],
        "pipeline": [
            "Median imputation with missing-value indicators",
            "Independent quantile estimator per target and quantile",
        ],
        "hyperparameters": {
            "loss": "quantile",
            "max_iter": 300,
            "learning_rate": 0.05,
            "max_leaf_nodes": 31,
            "l2_regularization": 1.0,
            "random_state": 17,
        },
        "validation": {
            "method": "Chronological train/validation/untouched-test split",
            "purging": "Rows whose outcome horizon crosses a later boundary are removed",
            "embargo": "One complete prediction horizon",
            "promotion_gate": "At least 85% empirical 90% interval coverage on untouched test data",
        },
        "importance_note": (
            "HistGradientBoostingRegressor does not expose a native feature_importances_ vector. "
            "Permutation importance must be computed on a separately protected evaluation set."
        ),
    }


def _model_b_algorithm() -> dict[str, Any]:
    return {
        "family": "Calibrated balanced multinomial logistic classification",
        "estimator": "sklearn.linear_model.LogisticRegression",
        "classes": [
            "UP_H1..UP_H8",
            "DOWN_H1..DOWN_H8",
            "NO_EVENT",
        ],
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "upper_barrier_return": TARGET_UP_RETURN,
        "lower_barrier_return": TARGET_DOWN_RETURN,
        "pipeline": [
            "Median imputation with missing-value indicators",
            "StandardScaler",
            "Balanced multinomial logistic regression",
            "Sigmoid probability calibration on a later temporal partition",
        ],
        "hyperparameters": {
            "max_iter": 2_000,
            "class_weight": "balanced",
            "random_state": 17,
            "calibration_method": "sigmoid",
        },
        "validation": {
            "method": (
                "One joint event-time model with chronological train/calibration/"
                "untouched-test and an eight-hour purge/embargo"
            ),
            "coherence": (
                "Hourly cumulative probabilities are sums from one categorical "
                "distribution and therefore cannot move backward"
            ),
            "decision_policy": (
                "Threshold selected on validation support/precision and verified "
                "unchanged on untouched test"
            ),
            "gate_methodology_version": COMPETING_RISK_GATE_VERSION,
        },
        "importance_note": (
            "The calibrated wrapper does not expose one directly comparable coefficient vector. "
            "Any coefficient or permutation analysis must preserve class, fold, and calibration context."
        ),
    }


class ModelLabService:
    """Read model evidence and run isolated what-if inference."""

    def __init__(self, platform: RealDataPlatformV2) -> None:
        self.platform = platform
        self._feature_cache_signature: tuple[int, int] | None = None
        self._feature_cache_payload: dict[str, Any] | None = None

    @property
    def discovery_path(self) -> Path:
        return self.platform.paths.reports.parent / "first_passage_discovery.json"

    @property
    def feature_diagnostics_path(self) -> Path:
        return self.platform.paths.feature_diagnostics

    def _model_bundle(self, model_key: str) -> dict[str, Any] | None:
        if model_key == MODEL_A_KEY:
            return self.platform.envelope.bundle
        if model_key == MODEL_B_KEY:
            return self.platform._bundle
        raise ValueError(f"unsupported model_key: {model_key}")

    def _model_report(self, model_key: str) -> dict[str, Any]:
        if model_key == MODEL_A_KEY:
            return _json_object(self.platform.envelope.paths.report)
        if model_key == MODEL_B_KEY:
            return _json_object(self.platform.paths.reports)
        raise ValueError(f"unsupported model_key: {model_key}")

    def _descriptor(self, model_key: str) -> dict[str, Any]:
        bundle = self._model_bundle(model_key)
        available = _bundle_horizons(bundle)
        configured = list(RESEARCH_HORIZONS_MINUTES)
        is_training = (
            model_key == MODEL_A_KEY
            and self.platform.status.lifecycle_stage == "TRAIN_MODEL_A"
        ) or (
            model_key == MODEL_B_KEY
            and self.platform.status.lifecycle_stage == "TRAIN_MODEL_B"
        )
        return {
            "key": model_key,
            "display_name": (
                "Model A — Adaptive Shock Magnitude"
                if model_key == MODEL_A_KEY
                else f"Model B — ±{TARGET_PERCENT}% First Touch"
            ),
            "state": "TRAINING" if is_training else "RESEARCH_READY" if available else "WAIT",
            "runtime_state": self.platform.status.state,
            "runtime_reason": self.platform.status.reason,
            "lifecycle_progress": self.platform.status.lifecycle_progress if is_training else None,
            "available": bool(available),
            "available_horizons": available,
            "waiting_horizons": [value for value in configured if value not in available],
            "model_version": None if bundle is None else bundle.get("model_version"),
            "trained_at_ms": None if bundle is None else bundle.get("trained_at_ms"),
            "training_final_rows": None if bundle is None else bundle.get("training_final_rows"),
            "feature_schema_version": (
                FEATURE_SCHEMA_VERSION
                if bundle is None
                else bundle.get("feature_schema_version", FEATURE_SCHEMA_VERSION)
            ),
            "target_definition_version": TARGET_DEFINITION_VERSION,
            "target_up_return": TARGET_UP_RETURN,
            "target_down_return": TARGET_DOWN_RETURN,
            "feature_names": _feature_names(bundle),
            "algorithm": _model_a_algorithm() if model_key == MODEL_A_KEY else _model_b_algorithm(),
            "training_report": self._model_report(model_key),
            "promoted_for_trading": False,
        }

    def overview_payload(self) -> dict[str, Any]:
        price_stats = self.platform.price_store.stats()
        return {
            "status": "READY",
            "generated_at_ms": int(time.time() * 1000),
            "platform": {
                **asdict(self.platform.status),
                "symbol": self.platform.config.symbol,
                "configured_horizons_minutes": list(RESEARCH_HORIZONS_MINUTES),
                "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
                "target_definition_version": TARGET_DEFINITION_VERSION,
                "target_up_return": TARGET_UP_RETURN,
                "target_down_return": TARGET_DOWN_RETURN,
                "price_store": asdict(price_stats),
                "ready_for_trading": False,
            },
            "models": {
                MODEL_A_KEY: self._descriptor(MODEL_A_KEY),
                MODEL_B_KEY: self._descriptor(MODEL_B_KEY),
            },
            "statistical_analysis": {
                "first_passage": _json_object(self.discovery_path),
                "feature_diagnostics": _json_object(self.feature_diagnostics_path),
                "data_integrity": _json_object(
                    self.platform.paths.reports.parent / "data_integrity.json"
                ),
            },
            "laboratory_policy": {
                "predictions_are_persisted": False,
                "champion_is_modified": False,
                "automatic_trading": False,
                "manual_inputs_are_hypothetical": True,
                "current_market_inputs_use_last_completed_feature_row": True,
            },
        }

    def _feature_payload(self) -> dict[str, Any]:
        path = self.platform.paths.features
        if not path.exists():
            return {"status": "WAIT", "reason": "feature_dataset_not_available"}
        signature = (path.stat().st_mtime_ns, path.stat().st_size)
        if signature == self._feature_cache_signature and self._feature_cache_payload is not None:
            return self._feature_cache_payload

        frame = pd.read_parquet(path)
        if frame.empty:
            return {"status": "WAIT", "reason": "feature_dataset_empty"}
        if "timestamp_ms" not in frame.columns or "price" not in frame.columns:
            return {"status": "WAIT", "reason": "feature_dataset_missing_reference_columns"}

        frame = frame.sort_values("timestamp_ms", ignore_index=True)
        latest = frame.iloc[-1]
        ordered_names = list(
            dict.fromkeys(
                [
                    *_feature_names(self.platform.envelope.bundle),
                    *_feature_names(self.platform._bundle),
                    *audit_feature_columns(frame).eligible,
                ]
            )
        )
        specifications: list[dict[str, Any]] = []
        for name in ordered_names:
            if name not in frame.columns:
                specifications.append(
                    {
                        "name": name,
                        "latest": None,
                        "available": False,
                        "reason": "required_by_bundle_but_missing_from_feature_dataset",
                    }
                )
                continue
            numeric = pd.to_numeric(frame[name], errors="coerce")
            finite = numeric[np.isfinite(numeric)]
            if finite.empty:
                specifications.append(
                    {
                        "name": name,
                        "latest": _optional_float(latest.get(name)),
                        "available": False,
                        "non_null_rows": 0,
                        "missing_rate": 1.0,
                    }
                )
                continue
            specifications.append(
                {
                    "name": name,
                    "latest": _optional_float(latest.get(name)),
                    "available": True,
                    "non_null_rows": int(finite.size),
                    "missing_rate": float(1.0 - finite.size / len(frame)),
                    "minimum": float(finite.min()),
                    "p05": float(finite.quantile(0.05)),
                    "median": float(finite.median()),
                    "p95": float(finite.quantile(0.95)),
                    "maximum": float(finite.max()),
                }
            )

        payload: dict[str, Any] = {
            "status": "READY",
            "source": "latest_completed_causal_feature_row",
            "symbol": self.platform.config.symbol,
            "timestamp_ms": int(latest["timestamp_ms"]),
            "anchor_price": float(latest["price"]),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_rows": int(len(frame)),
            "features": specifications,
            "values": {item["name"]: item.get("latest") for item in specifications},
            "model_requirements": {
                MODEL_A_KEY: _feature_names(self.platform.envelope.bundle),
                MODEL_B_KEY: _feature_names(self.platform._bundle),
            },
        }
        self._feature_cache_signature = signature
        self._feature_cache_payload = payload
        return payload

    def current_inputs_payload(self) -> dict[str, Any]:
        return self._feature_payload()

    def _out_of_distribution(
        self,
        values: dict[str, float | None],
        required_names: list[str],
    ) -> dict[str, Any]:
        feature_payload = self._feature_payload()
        rows = feature_payload.get("features", [])
        by_name = {
            str(item.get("name")): item
            for item in rows
            if isinstance(item, dict) and item.get("name") is not None
        }
        outside: list[dict[str, Any]] = []
        missing: list[str] = []
        for name in required_names:
            value = values.get(name)
            if value is None:
                missing.append(name)
                continue
            stats = by_name.get(name, {})
            lower = _optional_float(stats.get("p05"))
            upper = _optional_float(stats.get("p95"))
            if lower is not None and upper is not None and not lower <= value <= upper:
                outside.append({"name": name, "value": value, "p05": lower, "p95": upper})
        return {
            "missing_feature_count": len(missing),
            "missing_features": missing,
            "outside_historical_p05_p95_count": len(outside),
            "outside_historical_p05_p95": outside,
            "note": "This is a descriptive range check, not a formal drift test.",
        }

    def predict_payload(
        self,
        *,
        model_key: str,
        horizon_minutes: int,
        input_source: str,
        anchor_price: float | None,
        feature_values: dict[str, float | None],
    ) -> dict[str, Any]:
        if model_key not in MODEL_KEYS:
            return {"status": "REJECTED", "reason": "unsupported_model_key"}
        if horizon_minutes not in RESEARCH_HORIZONS_MINUTES:
            return {"status": "REJECTED", "reason": "unsupported_horizon"}

        bundle = self._model_bundle(model_key)
        if bundle is None:
            return {
                "status": "WAIT",
                "reason": "no_governed_model_bundle_available",
                "model_key": model_key,
                "horizon_minutes": horizon_minutes,
                "persisted": False,
            }
        model = (
            bundle.get("joint_model")
            if model_key == MODEL_B_KEY
            else _model_for_horizon(bundle, horizon_minutes)
        )
        if model is None:
            return {
                "status": "WAIT",
                "reason": "requested_horizon_has_not_passed_its_independent_gate",
                "model_key": model_key,
                "horizon_minutes": horizon_minutes,
                "available_horizons": _bundle_horizons(bundle),
                "persisted": False,
            }

        current = self._feature_payload()
        values = dict(feature_values)
        effective_anchor = anchor_price
        effective_timestamp: int | None = None
        if input_source == "current_market":
            if current.get("status") != "READY":
                return {"status": "WAIT", "reason": "current_feature_row_not_available"}
            raw_values = current.get("values", {})
            if isinstance(raw_values, dict):
                values = {
                    str(key): _optional_float(value)
                    for key, value in raw_values.items()
                }
            effective_anchor = float(current["anchor_price"])
            effective_timestamp = int(current["timestamp_ms"])
        if effective_anchor is None or effective_anchor <= 0:
            return {"status": "REJECTED", "reason": "anchor_price_must_be_positive"}

        required_names = _feature_names(bundle)
        normalized_values = {
            name: _optional_float(values.get(name)) for name in required_names
        }
        row = pd.DataFrame([normalized_values], columns=required_names)
        ood = self._out_of_distribution(normalized_values, required_names)
        issued_at_ms = int(time.time() * 1000)

        if model_key == MODEL_A_KEY:
            estimates = predict_envelope(cast(dict[str, Any], model), row)
            maximum = sorted(
                [
                    estimates["future_max_return_q05"],
                    estimates["future_max_return_q50"],
                    estimates["future_max_return_q95"],
                ]
            )
            minimum = sorted(
                [
                    estimates["future_min_return_q05"],
                    estimates["future_min_return_q50"],
                    estimates["future_min_return_q95"],
                ]
            )
            close = sorted(
                [
                    estimates["future_close_return_q05"],
                    estimates["future_close_return_q50"],
                    estimates["future_close_return_q95"],
                ]
            )
            output: dict[str, Any] = {
                "max_return_q05": maximum[0],
                "max_return_q50": maximum[1],
                "max_return_q95": maximum[2],
                "min_return_q05": minimum[0],
                "min_return_q50": minimum[1],
                "min_return_q95": minimum[2],
                "close_return_q05": close[0],
                "close_return_q50": close[1],
                "close_return_q95": close[2],
                "max_price_q50": effective_anchor * (1.0 + maximum[1]),
                "min_price_q50": effective_anchor * (1.0 + minimum[1]),
                "close_price_q05": effective_anchor * (1.0 + close[0]),
                "close_price_q50": effective_anchor * (1.0 + close[1]),
                "close_price_q95": effective_anchor * (1.0 + close[2]),
                "target_up_return": TARGET_UP_RETURN,
                "target_up_price": effective_anchor * (1.0 + TARGET_UP_RETURN),
                "target_up_reached_by_median": maximum[1] >= TARGET_UP_RETURN,
                "target_up_reached_by_upper_bound": maximum[2] >= TARGET_UP_RETURN,
            }
        else:
            timeline = predict_hourly_competing_risks(model, row)
            projection = next(
                item
                for item in timeline
                if int(item["horizon_minutes"]) == horizon_minutes
            )
            mapped = {
                "UP_02": float(projection["p_up_02"]),
                "DOWN_02": float(projection["p_down_02"]),
                "NO_EVENT": float(projection["p_no_event"]),
            }
            output = {
                "p_up_02": mapped["UP_02"],
                "p_down_02": mapped["DOWN_02"],
                "p_no_event": mapped["NO_EVENT"],
                "highest_probability_class": max(mapped, key=mapped.__getitem__),
                "highest_probability": max(mapped.values()),
                "hourly_timeline": timeline,
            }

        return {
            "status": "RESEARCH_RESULT",
            "model_key": model_key,
            "model_version": bundle.get("model_version"),
            "horizon_minutes": horizon_minutes,
            "issued_at_ms": issued_at_ms,
            "input_source": input_source,
            "feature_timestamp_ms": effective_timestamp,
            "anchor_price": effective_anchor,
            "required_feature_count": len(required_names),
            "input_diagnostics": ood,
            "output": output,
            "decision": "WAIT",
            "decision_reason": "isolated_model_lab_experiment_not_a_live_trading_prediction",
            "persisted": False,
            "promoted_for_trading": False,
        }


__all__ = ["MODEL_A_KEY", "MODEL_B_KEY", "MODEL_KEYS", "ModelLabService"]
