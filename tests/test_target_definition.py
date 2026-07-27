from __future__ import annotations

import json
from pathlib import Path

from xasp.target_definition import (
    TARGET_DEFINITION_FILENAME,
    TARGET_DEFINITION_VERSION,
    TARGET_DOWN_RETURN,
    TARGET_UP_RETURN,
    ensure_current_target_definition,
)


def test_two_percent_target_invalidates_only_derived_artifacts(tmp_path: Path) -> None:
    data = tmp_path / "data"
    models = tmp_path / "models"
    reports = tmp_path / "reports"
    prices = data / "prices"
    anchors = data / "anchors"
    prices.mkdir(parents=True)
    anchors.mkdir(parents=True)
    models.mkdir()
    reports.mkdir()
    (prices / "2026-07.parquet").write_bytes(b"raw-price-history")
    (anchors / "manifest.json").write_text("{}", encoding="utf-8")
    (models / "champion.joblib").write_bytes(b"old-model")
    (models / "envelope_champion.joblib").write_bytes(b"old-envelope")
    (data / "predictions.parquet").write_bytes(b"old-ledger")
    (reports / "training.json").write_text("{}", encoding="utf-8")
    (reports / "envelope_training.json").write_text("{}", encoding="utf-8")
    (data / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "xasp-default",
                "raw_watermarks_ms": {"binance_spot:XRPUSDT:kline_1m": 123},
                "feature_watermark_ms": 123,
                "finalized_label_watermark_ms": 120,
                "last_training_cutoff_ms": 100,
                "last_model_version": "old",
                "pending_label_count": 2,
                "finalized_label_count": 10,
                "updated_at": "2026-07-28T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = ensure_current_target_definition(
        data_dir=data,
        models_dir=models,
        reports_dir=reports,
    )

    assert result.changed is True
    assert (prices / "2026-07.parquet").read_bytes() == b"raw-price-history"
    assert not anchors.exists()
    assert not (models / "champion.joblib").exists()
    assert not (models / "envelope_champion.joblib").exists()
    assert not (data / "predictions.parquet").exists()
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    assert state["raw_watermarks_ms"] == {"binance_spot:XRPUSDT:kline_1m": 123}
    assert state["finalized_label_watermark_ms"] is None
    assert state["last_model_version"] is None
    marker = json.loads(
        (data / TARGET_DEFINITION_FILENAME).read_text(encoding="utf-8")
    )
    assert marker["version"] == TARGET_DEFINITION_VERSION
    assert marker["upper_return"] == TARGET_UP_RETURN == 0.02
    assert marker["lower_return"] == TARGET_DOWN_RETURN == -0.02


def test_current_target_activation_is_idempotent(tmp_path: Path) -> None:
    first = ensure_current_target_definition(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        reports_dir=tmp_path / "reports",
    )
    second = ensure_current_target_definition(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        reports_dir=tmp_path / "reports",
    )

    assert first.changed is True
    assert second.changed is False
    assert second.previous_version == TARGET_DEFINITION_VERSION
    assert second.removed_paths == ()
