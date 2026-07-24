from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

import xasp.prediction_ledger as prediction_ledger_module
from xasp.labeling import CandlePoint, FirstTouchResult, PricePoint
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


def test_concurrent_appends_do_not_lose_predictions(tmp_path: Path) -> None:
    path = tmp_path / "predictions.parquet"
    records = [
        make_record(
            created_at_ms=(index + 1) * 60_000,
            anchor_timestamp_ms=(index + 1) * 60_000,
        )
        for index in range(12)
    ]

    def append(record: PredictionRecord) -> None:
        PredictionLedger(path).append([record])

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(append, records))

    saved = PredictionLedger(path).load()
    assert len(saved) == len(records)
    assert saved["prediction_id"].nunique() == len(records)
    assert not path.with_suffix(".parquet.lock").exists()


def test_maturation_computes_without_blocking_reads_or_appends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "predictions.parquet"
    ledger = PredictionLedger(path)
    first = make_record()
    second = make_record(
        created_at_ms=2 * 60_000,
        anchor_timestamp_ms=2 * 60_000,
    )
    ledger.append([first])
    candles = [
        CandlePoint(
            timestamp_ms=minute * 60_000,
            open=1.0,
            high=1.01,
            low=0.99,
            close=1.0,
        )
        for minute in range(2, 17)
    ]
    computation_started = Event()
    release_computation = Event()
    original_label = prediction_ledger_module.label_first_touch_candles

    def blocking_label(*args: object, **kwargs: object) -> FirstTouchResult:
        computation_started.set()
        if not release_computation.wait(timeout=5):
            raise TimeoutError("test did not release maturation computation")
        return original_label(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        prediction_ledger_module,
        "label_first_touch_candles",
        blocking_label,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(
            ledger.mature_candles,
            candles,
            16 * 60_000,
        )
        assert computation_started.wait(timeout=2)
        concurrent = PredictionLedger(path, lock_timeout_s=0)
        try:
            assert len(concurrent.load()) == 1
            concurrent.append([second])
        finally:
            release_computation.set()
        matured = future.result(timeout=5)

    first_row = matured[matured["prediction_id"] == first.prediction_id].iloc[0]
    second_row = matured[matured["prediction_id"] == second.prediction_id].iloc[0]
    assert first_row["status"] == "FINAL"
    assert second_row["status"] == "PENDING"
    assert not path.with_suffix(".parquet.lock").exists()


def test_windows_replace_permission_error_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "predictions.parquet"
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(source: Path, target: Path) -> Path:
        nonlocal attempts
        if target == path and attempts < 2:
            attempts += 1
            raise PermissionError("simulated Windows file lock")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    ledger = PredictionLedger(path, replace_retries=3, retry_delay_s=0)

    saved = ledger.append([make_record()])

    assert len(saved) == 1
    assert attempts == 2
    assert list(tmp_path.glob(".predictions.parquet.*.tmp")) == []


def test_temporary_file_is_cleaned_when_replace_never_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "predictions.parquet"

    def denied_replace(source: Path, target: Path) -> Path:
        raise PermissionError("simulated persistent Windows file lock")

    monkeypatch.setattr(Path, "replace", denied_replace)
    ledger = PredictionLedger(path, replace_retries=2, retry_delay_s=0)

    with pytest.raises(PermissionError, match="persistent Windows"):
        ledger.append([make_record()])

    assert list(tmp_path.glob(".predictions.parquet.*.tmp")) == []
    assert not path.with_suffix(".parquet.lock").exists()
