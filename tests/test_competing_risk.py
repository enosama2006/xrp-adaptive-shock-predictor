from __future__ import annotations

import numpy as np
import pandas as pd

from xasp.competing_risk import (
    CompetingRiskConfig,
    build_competing_risk_dataset,
    predict_hourly_competing_risks,
    train_competing_risk_model,
)

MINUTE = 60_000


class _FixedJointModel:
    classes_ = np.asarray(["DOWN_H1", "UP_H2", "NO_EVENT"])

    def predict_proba(self, _: pd.DataFrame) -> np.ndarray:
        return np.asarray([[0.10, 0.30, 0.60]])


def test_joint_target_uses_actual_first_touch_hour_from_eight_hour_row() -> None:
    frame = pd.DataFrame(
        [
            {
                "anchor_timestamp_ms": 0,
                "horizon_minutes": 480,
                "horizon_end_ms": 480 * MINUTE,
                "label": "UP_02",
                "status": "FINAL",
                "touch_timestamp_ms": 61 * MINUTE,
                "feature": 1.0,
            },
            {
                "anchor_timestamp_ms": MINUTE,
                "horizon_minutes": 480,
                "horizon_end_ms": 481 * MINUTE,
                "label": "NO_EVENT",
                "status": "FINAL",
                "touch_timestamp_ms": None,
                "feature": 0.0,
            },
        ]
    )

    dataset, audit = build_competing_risk_dataset(frame, ["feature"])

    assert dataset["event_class"].tolist() == ["UP_H2", "NO_EVENT"]
    assert audit["usable_rows"] == 2


def test_joint_projection_is_a_coherent_hourly_timeline() -> None:
    timeline = predict_hourly_competing_risks(
        _FixedJointModel(),
        pd.DataFrame([{"feature": 1.0}]),
    )

    assert len(timeline) == 8
    assert timeline[0]["p_up_02"] == 0.0
    assert timeline[0]["p_down_02"] == 0.10
    assert timeline[0]["p_no_event"] == 0.90
    assert timeline[1]["p_up_02"] == 0.30
    assert timeline[1]["p_down_02"] == 0.10
    assert timeline[1]["p_no_event"] == 0.60
    assert all(
        float(current["event_probability"]) <= float(following["event_probability"])
        for current, following in zip(timeline, timeline[1:], strict=False)
    )
    assert all(
        float(current["p_no_event"]) >= float(following["p_no_event"])
        for current, following in zip(timeline, timeline[1:], strict=False)
    )


def test_joint_model_trains_once_for_the_complete_eight_hour_question() -> None:
    rows = 1_200
    anchors = np.arange(rows, dtype=np.int64) * 10 * MINUTE
    labels = np.asarray(["UP_02", "DOWN_02", "NO_EVENT"] * (rows // 3))
    touch = np.where(
        labels == "UP_02",
        anchors + 30 * MINUTE,
        np.where(labels == "DOWN_02", anchors + 90 * MINUTE, np.nan),
    )
    feature = np.where(labels == "UP_02", 1.0, np.where(labels == "DOWN_02", -1.0, 0.0))
    frame = pd.DataFrame(
        {
            "anchor_timestamp_ms": anchors,
            "horizon_minutes": 480,
            "horizon_end_ms": anchors + 480 * MINUTE,
            "label": labels,
            "status": "FINAL",
            "touch_timestamp_ms": touch,
            "feature": feature,
        }
    )

    model, report = train_competing_risk_model(
        frame,
        ["feature"],
        CompetingRiskConfig(
            minimum_rows=300,
            minimum_directional_signals=5,
            minimum_signals_per_direction=1,
        ),
    )

    assert model is not None
    assert report.status == "RESEARCH_READY"
    assert report.metrics["advisory_gate"]["status"] == "PASS"
