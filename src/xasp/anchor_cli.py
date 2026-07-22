"""CLI for incrementally updating the persistent anchor training dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .anchor_dataset import AnchorDatasetConfig, AnchorDatasetStore, update_anchor_dataset
from .dataset_state import DatasetStateStore
from .labeling import PricePoint


def _load_price_points(path: Path, timestamp_column: str, price_column: str) -> list[PricePoint]:
    frame = pd.read_parquet(path, columns=[timestamp_column, price_column])
    frame = frame.dropna().sort_values(timestamp_column)
    return [
        PricePoint(timestamp_ms=int(timestamp), price=float(price))
        for timestamp, price in frame[[timestamp_column, price_column]].itertuples(
            index=False, name=None
        )
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Incrementally append anchors and finalize matured ±10% labels."
    )
    parser.add_argument("--prices", type=Path, required=True, help="Input Parquet price path")
    parser.add_argument("--anchors", type=Path, required=True, help="Persistent anchor Parquet path")
    parser.add_argument("--state", type=Path, required=True, help="Persistent JSON state path")
    parser.add_argument("--timestamp-column", default="timestamp_ms")
    parser.add_argument("--price-column", default="price")
    parser.add_argument("--cadence-seconds", type=int, default=60)
    parser.add_argument("--horizons", default="15,30,45,60")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    horizons = tuple(int(value.strip()) for value in args.horizons.split(",") if value.strip())
    config = AnchorDatasetConfig(
        horizons_minutes=horizons,
        cadence_ms=args.cadence_seconds * 1000,
    )
    frame = update_anchor_dataset(
        _load_price_points(args.prices, args.timestamp_column, args.price_column),
        AnchorDatasetStore(args.anchors),
        DatasetStateStore(args.state),
        config,
    )
    pending = int((frame["status"] == "PENDING").sum())
    finalized = int((frame["status"] == "FINAL").sum())
    excluded = int((frame["status"] == "EXCLUDED").sum())
    print(
        f"rows={len(frame)} finalized={finalized} pending={pending} excluded={excluded} "
        f"last_anchor_ms={int(frame['anchor_timestamp_ms'].max()) if not frame.empty else 'none'}"
    )


if __name__ == "__main__":
    main()
