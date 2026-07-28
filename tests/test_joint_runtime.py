from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from xasp.anchor_dataset import ANCHOR_COLUMNS, AnchorDatasetStore
from xasp.platform_runtime import RuntimeConfig, RuntimePaths
from xasp.platform_runtime_v2 import RealDataPlatformV2

MINUTE = 60_000


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        prices=tmp_path / "data" / "prices.parquet",
        anchors=tmp_path / "data" / "anchors.parquet",
        features=tmp_path / "data" / "features.parquet",
        state=tmp_path / "data" / "state.json",
        models=tmp_path / "models" / "champion.joblib",
        reports=tmp_path / "reports" / "training.json",
        feature_diagnostics=tmp_path / "reports" / "feature_diagnostics.json",
        ledger=tmp_path / "data" / "predictions.parquet",
        status=tmp_path / "data" / "platform_status.json",
    )


def test_runtime_trains_one_joint_model_and_emits_eight_coherent_rows(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    platform = RealDataPlatformV2(
        paths,
        RuntimeConfig(
            bootstrap_start_ms=0,
            minimum_final_rows_per_horizon=300,
            retrain_after_new_final_rows=1,
        ),
    )
    rows = 1_200
    anchors = np.arange(rows, dtype=np.int64) * 10 * MINUTE
    labels = np.asarray(["UP_02", "DOWN_02", "NO_EVENT"] * (rows // 3))
    touch = np.where(
        labels == "UP_02",
        anchors + 30 * MINUTE,
        np.where(labels == "DOWN_02", anchors + 90 * MINUTE, np.nan),
    )
    feature = np.where(labels == "UP_02", 1.0, np.where(labels == "DOWN_02", -1.0, 0.0))
    anchor_frame = pd.DataFrame(
        {
            "anchor_timestamp_ms": anchors,
            "anchor_price": 1.0,
            "horizon_minutes": 480,
            "horizon_end_ms": anchors + 480 * MINUTE,
            "upper_barrier_price": 1.02,
            "lower_barrier_price": 0.98,
            "max_price": np.where(labels == "UP_02", 1.03, 1.01),
            "min_price": np.where(labels == "DOWN_02", 0.97, 0.99),
            "horizon_close_price": 1.0,
            "max_return": np.where(labels == "UP_02", 0.03, 0.01),
            "min_return": np.where(labels == "DOWN_02", -0.03, -0.01),
            "horizon_close_return": 0.0,
            "label": labels,
            "touch_timestamp_ms": touch,
            "touch_price": np.where(
                labels == "UP_02",
                1.02,
                np.where(labels == "DOWN_02", 0.98, np.nan),
            ),
            "status": "FINAL",
            "reason": "test",
        }
    ).reindex(columns=ANCHOR_COLUMNS)
    AnchorDatasetStore(paths.anchors).upsert(anchor_frame)
    paths.features.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp_ms": anchors,
            "price": 1.0,
            "return_1m": feature,
            "feature_available_at_ms": anchors,
        }
    ).to_parquet(paths.features, index=False)

    trained = platform.train_if_due(force=True)
    predictions = platform.predict_latest(now_ms=int(anchors[-1] + MINUTE))

    assert trained is True
    assert platform._bundle is not None
    assert platform._bundle["joint_model"] is not None
    assert "models" not in platform._bundle
    assert len(predictions) == 8
    events = [
        float(row["p_up_02"]) + float(row["p_down_02"])
        for row in predictions
    ]
    no_touch = [float(row["p_no_event"]) for row in predictions]
    assert events == sorted(events)
    assert no_touch == sorted(no_touch, reverse=True)
