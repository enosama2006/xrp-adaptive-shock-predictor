"""Governed target definition and one-time derived-artifact invalidation.

Raw observed market data is intentionally preserved.  Any artifact whose
meaning depends on the target barrier is removed when the target definition
changes so old labels and models cannot be mixed with the current objective.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TARGET_UP_RETURN = 0.02
TARGET_DOWN_RETURN = -0.02
TARGET_PERCENT = 2
TARGET_UP_LABEL = "UP_02"
TARGET_DOWN_LABEL = "DOWN_02"
TARGET_DEFINITION_VERSION = "xrp-symmetric-first-touch-2pct-v1"
TARGET_DEFINITION_FILENAME = "target_definition.json"


@dataclass(frozen=True, slots=True)
class TargetDefinition:
    version: str = TARGET_DEFINITION_VERSION
    upper_return: float = TARGET_UP_RETURN
    lower_return: float = TARGET_DOWN_RETURN
    primary_objective: str = "detect_upside_move_of_at_least_2_percent"
    comparison_event: str = "downside_move_of_at_least_2_percent"
    first_touch_classes: tuple[str, str, str] = (
        TARGET_UP_LABEL,
        TARGET_DOWN_LABEL,
        "NO_EVENT",
    )


@dataclass(frozen=True, slots=True)
class TargetMigrationResult:
    changed: bool
    previous_version: str | None
    current_version: str
    removed_paths: tuple[str, ...]
    preserved_raw_price_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_version(marker: Path) -> str | None:
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid"
    if not isinstance(payload, dict):
        return "invalid"
    value = payload.get("version")
    return None if value is None else str(value)


def _remove(path: Path, removed: list[str]) -> None:
    if path.is_dir():
        shutil.rmtree(path)
        removed.append(str(path))
    elif path.exists():
        path.unlink()
        removed.append(str(path))


def _reset_dataset_state(path: Path) -> None:
    """Preserve raw ingestion watermarks while clearing target/model state."""

    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return
    if not isinstance(payload, dict):
        path.unlink(missing_ok=True)
        return
    payload.update(
        {
            "feature_watermark_ms": None,
            "finalized_label_watermark_ms": None,
            "last_training_cutoff_ms": None,
            "last_model_version": None,
            "pending_label_count": 0,
            "finalized_label_count": 0,
        }
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def ensure_current_target_definition(
    *,
    data_dir: Path = Path("data"),
    models_dir: Path = Path("models"),
    reports_dir: Path = Path("reports"),
) -> TargetMigrationResult:
    """Invalidate only target-derived artifacts when the barrier definition changes."""

    marker = data_dir / TARGET_DEFINITION_FILENAME
    previous = _read_version(marker)
    current = TARGET_DEFINITION_VERSION
    preserved = (
        str(data_dir / "prices"),
        str(data_dir / "prices.parquet"),
    )
    if previous == current:
        return TargetMigrationResult(False, previous, current, (), preserved)

    removed: list[str] = []
    threshold_artifacts = (
        data_dir / "anchors",
        data_dir / "anchors.parquet",
        data_dir / "future_envelopes",
        data_dir / "future_envelopes.parquet",
        data_dir / "predictions.parquet",
        data_dir / "envelope_predictions.parquet",
        models_dir / "champion.joblib",
        models_dir / "envelope_champion.joblib",
        reports_dir / "training.json",
        reports_dir / "envelope_training.json",
    )
    invalidation_required = previous is not None or any(
        path.exists() for path in threshold_artifacts
    )
    derived_paths = (
        *threshold_artifacts,
        data_dir / "predictions.parquet.lock",
        data_dir / "platform_status.json",
        reports_dir / "first_passage_discovery.json",
        reports_dir / "production",
    )
    if invalidation_required:
        for path in derived_paths:
            _remove(path, removed)
        for path in data_dir.glob("anchors.before-*.parquet"):
            _remove(path, removed)
        _reset_dataset_state(data_dir / "state.json")
    data_dir.mkdir(parents=True, exist_ok=True)
    marker_payload = {
        **asdict(TargetDefinition()),
        "activated_at_ms": int(time.time() * 1000),
        "previous_version": previous,
        "removed_paths": removed,
        "preserved_raw_price_paths": list(preserved),
    }
    temporary = marker.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(marker_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(marker)

    reports_dir.mkdir(parents=True, exist_ok=True)
    migration_report = reports_dir / "target_definition_migration.json"
    migration_report.write_text(
        json.dumps(marker_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return TargetMigrationResult(True, previous, current, tuple(removed), preserved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Activate the governed XASP 2% target and invalidate stale derivatives."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = ensure_current_target_definition(
        data_dir=args.data_dir,
        models_dir=args.models_dir,
        reports_dir=args.reports_dir,
    )
    print(json.dumps(result.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
