"""CLI for incremental feature construction and guarded baseline training."""

from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import pandas as pd

from .baseline import BaselineConfig, train_multinomial_baseline
from .features import build_price_features, join_anchors_with_features


def _feature_columns(frame: pd.DataFrame) -> list[str]:
    excluded = {
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
    return [column for column in frame.columns if column not in excluded]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build causal features and train XASP baseline")
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--minimum-rows", type=int, default=500)
    args = parser.parse_args()

    prices = pd.read_parquet(args.prices)
    anchors = pd.read_parquet(args.anchors)
    features = build_price_features(prices)
    args.features.parent.mkdir(parents=True, exist_ok=True)
    features.to_parquet(args.features, index=False)

    modeling = join_anchors_with_features(anchors, features)
    feature_names = _feature_columns(modeling)
    model, report = train_multinomial_baseline(
        modeling,
        feature_names,
        BaselineConfig(minimum_rows=args.minimum_rows),
    )
    report.save(args.report)

    if model is not None:
        args.model.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": model,
                "feature_names": feature_names,
                "report_status": report.status,
            },
            args.model,
        )
    print(f"status={report.status} reason={report.reason} rows={report.row_count}")


if __name__ == "__main__":
    main()
