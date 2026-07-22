"""Real-only XASP platform supervisor.

This process never fabricates market data. It incrementally backfills Binance minute data,
updates rolling labels, trains only from persisted real rows, and writes a health snapshot.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys
import time


@dataclass(frozen=True, slots=True)
class PlatformPaths:
    prices: Path
    anchors: Path
    state: Path
    features: Path
    model: Path
    report: Path
    health: Path


def _run_module(module: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_health(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run XASP using real exchange data only")
    parser.add_argument("--bootstrap-start-ms", type=int, required=True)
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--cycle-seconds", type=int, default=60)
    parser.add_argument("--train-every-cycles", type=int, default=60)
    parser.add_argument("--minimum-training-rows", type=int, default=500)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--prices", type=Path, default=Path("data/prices.parquet"))
    parser.add_argument("--anchors", type=Path, default=Path("data/anchors.parquet"))
    parser.add_argument("--state", type=Path, default=Path("data/state.json"))
    parser.add_argument("--features", type=Path, default=Path("data/features.parquet"))
    parser.add_argument("--model", type=Path, default=Path("models/baseline.joblib"))
    parser.add_argument("--report", type=Path, default=Path("reports/baseline.json"))
    parser.add_argument("--health", type=Path, default=Path("runtime/health.json"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.cycle_seconds < 10:
        raise ValueError("cycle-seconds must be at least 10")
    if args.train_every_cycles <= 0:
        raise ValueError("train-every-cycles must be positive")

    paths = PlatformPaths(
        prices=args.prices,
        anchors=args.anchors,
        state=args.state,
        features=args.features,
        model=args.model,
        report=args.report,
        health=args.health,
    )
    cycle = 0
    while True:
        cycle += 1
        started_ms = int(time.time() * 1000)
        end_ms = started_ms - 60_000  # only request fully closed minute bars
        pipeline = _run_module(
            "xasp.pipeline_cli",
            [
                "--bootstrap-start-ms",
                str(args.bootstrap_start_ms),
                "--end-ms",
                str(end_ms),
                "--symbol",
                args.symbol,
                "--prices",
                str(paths.prices),
                "--anchors",
                str(paths.anchors),
                "--state",
                str(paths.state),
            ],
        )

        training: subprocess.CompletedProcess[str] | None = None
        should_train = pipeline.returncode == 0 and cycle % args.train_every_cycles == 0
        if should_train:
            training = _run_module(
                "xasp.baseline_cli",
                [
                    "--prices",
                    str(paths.prices),
                    "--anchors",
                    str(paths.anchors),
                    "--features",
                    str(paths.features),
                    "--model",
                    str(paths.model),
                    "--report",
                    str(paths.report),
                    "--minimum-rows",
                    str(args.minimum_training_rows),
                ],
            )

        health = {
            "schema_version": 1,
            "mode": "REAL_ONLY",
            "synthetic_data_allowed": False,
            "symbol": args.symbol,
            "cycle": cycle,
            "started_at_ms": started_ms,
            "closed_bar_cutoff_ms": end_ms,
            "pipeline_returncode": pipeline.returncode,
            "pipeline_stdout": pipeline.stdout[-4000:],
            "pipeline_stderr": pipeline.stderr[-4000:],
            "training_attempted": training is not None,
            "training_returncode": None if training is None else training.returncode,
            "training_stdout": "" if training is None else training.stdout[-4000:],
            "training_stderr": "" if training is None else training.stderr[-4000:],
            "paths": {key: str(value) for key, value in asdict(paths).items()},
            "official_action": "WAIT",
            "reason": "research_and_validation_not_yet_promoted",
        }
        _write_health(paths.health, health)

        if pipeline.returncode != 0:
            print(json.dumps(health, indent=2), file=sys.stderr)
        else:
            print(json.dumps(health, indent=2))

        if args.once:
            raise SystemExit(0 if pipeline.returncode == 0 else pipeline.returncode)
        time.sleep(args.cycle_seconds)


if __name__ == "__main__":
    main()
