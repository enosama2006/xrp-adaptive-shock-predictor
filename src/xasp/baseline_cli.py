"""Train the governed baseline from persisted real market data only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import pandas as pd

from .baseline import BaselineConfig, train_multinomial_baseline
from .features import build_price_features, join_anchors_with_features

NON_FEATURE_COLUMNS = {
    "timestamp_ms",
    "price",
    "feature_available_at_ms",
    "anchor_timestamp_ms",
    "anchor_price",
    "horizon_minutes",
    "horizon_end_ms",
    "upper_barrier_price",
    "lower_barrier_price",
    "max_price",
    "min_price",
    "max_return",
    "min_return",
    "label",
    "touch_timestamp_ms",
    "touch_price",
    "status",
    "reason",
}


def _validate_real_prices(frame: pd.DataFrame) -> None:
    required = {"timestamp_ms", "price"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"price file missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("real price file is empty")
    if "is_synthetic" in frame.columns and frame["is_synthetic"].fillna(False).astype(bool).any():
        raise ValueError("synthetic rows are forbidden")
    if "source" in frame.columns:
        sources = set(frame["source"].dropna().astype(str).str.lower())
        if any("synthetic" in value or "mock" in value or "simulated" in value for value in sources):
            raise ValueError("synthetic/mock/simulated sources are forbidden")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train XASP baseline on persisted real data")
    parser.add_argument("--prices", type=Path, default=Path("data/prices.parquet"))
    parser.add_argument("--anchors", type=Path, default=Path("data/anchors.parquet"))
    parser.add_argument("--features", type=Path, default=Path("data/features.parquet"))
    parser.add_argument("--model", type=Path, default=Path("models/baseline.joblib"))
    parser.add_argument("--report", type=Path, default=Path("reports/baseline.json"))
    parser.add_argument("--minimum-rows", type=int, default=500)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    prices = pd.read_parquet(args.prices)
    anchors = pd.read_parquet(args.anchors)
    _validate_real_prices(prices)

    features = build_price_features(prices)
    args.features.parent.mkdir(parents=True, exist_ok=True)
    features.to_parquet(args.features, index=False)

    matrix = join_anchors_with_features(anchors, features)
    feature_names = [
        column
        for column in matrix.columns
        if column not in NON_FEATURE_COLUMNS
        and pd.api.types.is_numeric_dtype(matrix[column])
    ]
    model, report = train_multinomial_baseline(
        matrix,
        feature_names,
        BaselineConfig(minimum_rows=args.minimum_rows),
    )
    report.save(args.report)
    if model is not None:
        args.model.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, args.model)

    print(json.dumps({"status": report.status, "reason": report.reason, "rows": report.row_count}))


if __name__ == "__main__":
    main()
