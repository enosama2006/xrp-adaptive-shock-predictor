from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .pipeline import IncrementalResearchPipeline, PipelineConfig, PipelinePaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resume XRP minute data and anchor labels")
    parser.add_argument("--prices", type=Path, default=Path("data/prices.parquet"))
    parser.add_argument("--anchors", type=Path, default=Path("data/anchors.parquet"))
    parser.add_argument("--state", type=Path, default=Path("data/state.json"))
    parser.add_argument("--bootstrap-start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, default=None)
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--overlap-minutes", type=int, default=2)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    end_ms = args.end_ms if args.end_ms is not None else int(time.time() * 1000)
    pipeline = IncrementalResearchPipeline(
        PipelinePaths(prices=args.prices, anchors=args.anchors, state=args.state),
        PipelineConfig(
            symbol=args.symbol,
            bootstrap_start_ms=args.bootstrap_start_ms,
            overlap_minutes=args.overlap_minutes,
        ),
    )
    result = pipeline.run(end_ms)
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
