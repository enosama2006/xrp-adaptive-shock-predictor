"""Production monitoring reports from observed outcomes only."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class ProductionReportPaths:
    latest_json: Path = Path("reports/production/latest.json")
    history_jsonl: Path = Path("reports/production/history.jsonl")


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _first_touch_metrics(ledger: pd.DataFrame) -> dict[str, Any]:
    if ledger.empty:
        return {"status": "WAIT", "reason": "no_predictions", "evaluated_rows": 0}
    evaluated = ledger[(ledger["status"] == "FINAL") & ledger["actual_label"].notna()].copy()
    if evaluated.empty:
        return {"status": "WAIT", "reason": "no_matured_predictions", "evaluated_rows": 0}

    class_columns = {
        "UP_10": "p_up_10",
        "DOWN_10": "p_down_10",
        "NO_EVENT": "p_no_event",
    }
    evaluated["predicted_label"] = evaluated[[*class_columns.values()]].idxmax(axis=1).map(
        {column: label for label, column in class_columns.items()}
    )
    evaluated["predicted_probability"] = evaluated[[*class_columns.values()]].max(axis=1)
    evaluated["correct"] = evaluated["predicted_label"] == evaluated["actual_label"]

    per_horizon: dict[str, Any] = {}
    for horizon, group in evaluated.groupby("horizon_minutes"):
        high_conf = group[group["predicted_probability"] >= 0.85]
        per_class: dict[str, Any] = {}
        for label in class_columns:
            predicted = group[group["predicted_label"] == label]
            actual = group[group["actual_label"] == label]
            true_positive = int(((group["predicted_label"] == label) & (group["actual_label"] == label)).sum())
            per_class[label] = {
                "support": int(len(actual)),
                "predicted_count": int(len(predicted)),
                "precision": None if predicted.empty else true_positive / len(predicted),
                "recall": None if actual.empty else true_positive / len(actual),
            }
        brier: dict[str, float] = {}
        for label, column in class_columns.items():
            truth = (group["actual_label"] == label).astype(float)
            brier[label] = float(np.mean((group[column].astype(float) - truth) ** 2))
        per_horizon[str(int(horizon))] = {
            "evaluated_rows": int(len(group)),
            "accuracy": float(group["correct"].mean()),
            "high_confidence_rows": int(len(high_conf)),
            "high_confidence_accuracy": None if high_conf.empty else float(high_conf["correct"].mean()),
            "mean_confidence": float(group["predicted_probability"].mean()),
            "brier": brier,
            "per_class": per_class,
        }

    high_conf_all = evaluated[evaluated["predicted_probability"] >= 0.85]
    return {
        "status": "READY",
        "evaluated_rows": int(len(evaluated)),
        "accuracy": float(evaluated["correct"].mean()),
        "high_confidence_rows": int(len(high_conf_all)),
        "high_confidence_accuracy": None if high_conf_all.empty else float(high_conf_all["correct"].mean()),
        "per_horizon": per_horizon,
    }


def _envelope_metrics(predictions: pd.DataFrame, prices: pd.DataFrame) -> dict[str, Any]:
    if predictions.empty:
        return {"status": "WAIT", "reason": "no_envelope_predictions", "evaluated_rows": 0}
    if prices.empty:
        return {"status": "WAIT", "reason": "no_prices", "evaluated_rows": 0}

    prices = prices.sort_values("timestamp_ms", ignore_index=True)
    now_ms = int(prices["timestamp_ms"].max())
    rows: list[dict[str, Any]] = []
    for row in predictions.itertuples(index=False):
        end_ms = int(row.anchor_timestamp_ms) + int(row.horizon_minutes) * 60_000
        if end_ms > now_ms:
            continue
        path = prices[(prices["timestamp_ms"] > int(row.anchor_timestamp_ms)) & (prices["timestamp_ms"] <= end_ms)]
        if len(path) < int(row.horizon_minutes):
            continue
        observed_high = float(path["high"].max() if "high" in path.columns else path["price"].max())
        observed_low = float(path["low"].min() if "low" in path.columns else path["price"].min())
        max_return = observed_high / float(row.anchor_price) - 1.0
        min_return = observed_low / float(row.anchor_price) - 1.0
        max_covered = float(row.max_return_q05) <= max_return <= float(row.max_return_q95)
        min_covered = float(row.min_return_q05) <= min_return <= float(row.min_return_q95)
        rows.append(
            {
                "horizon": int(row.horizon_minutes),
                "max_covered": max_covered,
                "min_covered": min_covered,
                "max_abs_error": abs(max_return - float(row.max_return_q50)),
                "min_abs_error": abs(min_return - float(row.min_return_q50)),
            }
        )
    if not rows:
        return {"status": "WAIT", "reason": "no_matured_envelope_predictions", "evaluated_rows": 0}
    frame = pd.DataFrame(rows)
    per_horizon: dict[str, Any] = {}
    for horizon, group in frame.groupby("horizon"):
        per_horizon[str(int(horizon))] = {
            "evaluated_rows": int(len(group)),
            "max_interval_coverage": float(group["max_covered"].mean()),
            "min_interval_coverage": float(group["min_covered"].mean()),
            "joint_interval_coverage": float((group["max_covered"] & group["min_covered"]).mean()),
            "max_median_mae": float(group["max_abs_error"].mean()),
            "min_median_mae": float(group["min_abs_error"].mean()),
        }
    return {
        "status": "READY",
        "evaluated_rows": int(len(frame)),
        "max_interval_coverage": float(frame["max_covered"].mean()),
        "min_interval_coverage": float(frame["min_covered"].mean()),
        "joint_interval_coverage": float((frame["max_covered"] & frame["min_covered"]).mean()),
        "per_horizon": per_horizon,
    }


def build_production_report(
    *,
    ledger: pd.DataFrame,
    envelope_predictions: pd.DataFrame,
    prices: pd.DataFrame,
    runtime_status: dict[str, Any],
) -> dict[str, Any]:
    generated_at_ms = int(time.time() * 1000)
    first_touch = _first_touch_metrics(ledger)
    envelope = _envelope_metrics(envelope_predictions, prices)
    warnings: list[str] = []
    if first_touch.get("high_confidence_rows", 0) < 100:
        warnings.append("insufficient_high_confidence_first_touch_sample")
    if envelope.get("evaluated_rows", 0) < 100:
        warnings.append("insufficient_matured_envelope_sample")
    if runtime_status.get("state") == "WAIT":
        warnings.append(f"runtime_wait:{runtime_status.get('reason')}")
    return {
        "generated_at_ms": generated_at_ms,
        "scope": "observed_production_predictions_only",
        "first_touch": first_touch,
        "future_envelope": envelope,
        "runtime": runtime_status,
        "warnings": warnings,
        "trading_readiness": "WAIT" if warnings else "RESEARCH_MONITORING_READY",
        "note": "Metrics evaluate observed matured predictions; they do not guarantee future profitability.",
    }


def save_production_report(report: dict[str, Any], paths: ProductionReportPaths = ProductionReportPaths()) -> None:
    paths.latest_json.parent.mkdir(parents=True, exist_ok=True)
    temporary = paths.latest_json.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(paths.latest_json)
    with paths.history_jsonl.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True) + "\n")
