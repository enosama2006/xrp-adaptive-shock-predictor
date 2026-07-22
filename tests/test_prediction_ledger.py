from pathlib import Path

import pytest

from xasp.labeling import PricePoint
from xasp.prediction_ledger import PredictionLedger, PredictionRecord


def make_record(**overrides: object) -> PredictionRecord:
    values: dict[str, object] = {
        "created_at_ms": 60_000,
        "anchor_timestamp_ms": 60_000,
        "anchor_price": 1.0,
        "horizon_minutes": 15,
        "model_version": "model-v1",
        "dataset_id": "dataset-v1",
        "feature_schema_version": "features-v1",
        "p_up_10": 0.2,
        "p_down_10": 0.1,
        "p_no_event": 0.7,
    }
    values.update(overrides)
    return PredictionRecord(**values)  # type: ignore[arg-type]


def test_probabilities_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to one"):
        make_record(p_up_10=0.5)


def test_append_is_idempotent_for_identical_prediction(tmp_path: Path) -> None:
    ledger = PredictionLedger(tmp_path / "predictions.parquet")
    record = make_record()
    first = ledger.append([record])
    second = ledger.append([record])
    assert len(first) == 1
    assert len(second) == 1
    assert second.iloc[0]["status"] == "PENDING"


def test_collision_rejected_when_immutable_payload_changes(tmp_path: Path) -> None:
    ledger = PredictionLedger(tmp_path / "predictions.parquet")
    ledger.append([make_record()])
    with pytest.raises(ValueError, match="collision"):
        ledger.append([make_record(p_up_10=0.3, p_no_event=0.6)])


def test_pending_prediction_matures_after_horizon(tmp_path: Path) -> None:
    ledger = PredictionLedger(tmp_path / "predictions.parquet")
    ledger.append([make_record()])
    prices = [
        PricePoint(timestamp_ms=60_000, price=1.0),
        PricePoint(timestamp_ms=5 * 60_000, price=1.02),
        PricePoint(timestamp_ms=10 * 60_000, price=1.11),
        PricePoint(timestamp_ms=16 * 60_000, price=1.12),
    ]
    matured = ledger.mature(prices, resolved_at_ms=16 * 60_000)
    row = matured.iloc[0]
    assert row["status"] == "FINAL"
    assert row["actual_label"] == "UP_10"
    assert row["touch_timestamp_ms"] == 10 * 60_000


def test_prediction_remains_pending_before_horizon(tmp_path: Path) -> None:
    ledger = PredictionLedger(tmp_path / "predictions.parquet")
    ledger.append([make_record()])
    pending = ledger.mature(
        [PricePoint(timestamp_ms=5 * 60_000, price=1.01)],
        resolved_at_ms=10 * 60_000,
    )
    assert pending.iloc[0]["status"] == "PENDING"
