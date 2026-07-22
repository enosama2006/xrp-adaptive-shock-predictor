import pandas as pd

from xasp.baseline import (
    FIRST_TOUCH_GATE_VERSION,
    BaselineConfig,
    train_multinomial_baseline,
)


def _dataset(rows: int) -> pd.DataFrame:
    labels = ["UP_10", "DOWN_10", "NO_EVENT"]
    label_series = [labels[i % 3] for i in range(rows)]
    feature_map = {
        "UP_10": (0.025, 0.012),
        "DOWN_10": (-0.025, 0.012),
        "NO_EVENT": (0.0, 0.002),
    }
    return pd.DataFrame(
        {
            "anchor_timestamp_ms": [i * 60_000 for i in range(rows)],
            "status": ["FINAL"] * rows,
            "label": label_series,
            "return_1m": [
                feature_map[label][0] + ((i % 5) - 2) * 0.0001
                for i, label in enumerate(label_series)
            ],
            "volatility_5m": [
                feature_map[label][1] + (i % 3) * 0.00005
                for i, label in enumerate(label_series)
            ],
        }
    )


def test_insufficient_rows_fail_closed_to_wait() -> None:
    model, report = train_multinomial_baseline(
        _dataset(60),
        ["return_1m", "volatility_5m"],
        BaselineConfig(minimum_rows=100),
    )
    assert model is None
    assert report.status == "WAIT"
    assert report.reason == "insufficient_final_rows"


def test_temporal_baseline_trains_when_directional_gate_passes() -> None:
    model, report = train_multinomial_baseline(
        _dataset(600),
        ["return_1m", "volatility_5m"],
        BaselineConfig(minimum_rows=500),
    )
    assert model is not None
    assert report.status == "RESEARCH_ONLY"
    assert report.reason == "directional_empirical_85pct_gate_passed_not_trading_promoted"
    assert report.train_rows < report.row_count
    assert report.test_rows > 0
    assert report.metrics["gate_methodology_version"] == FIRST_TOUCH_GATE_VERSION
    assert report.metrics["directional_high_confidence_predictions"] >= 20
    assert report.metrics["directional_high_confidence_empirical_precision"] >= 0.85
    assert report.metrics["probability_sum_max_error"] < 1e-9
    assert set(report.metrics["per_class"]) == {"UP_10", "DOWN_10", "NO_EVENT"}


def test_dominant_no_event_accuracy_cannot_pass_directional_gate() -> None:
    rows = 600
    frame = pd.DataFrame(
        {
            "anchor_timestamp_ms": [i * 60_000 for i in range(rows)],
            "status": ["FINAL"] * rows,
            "label": ["UP_10"] * 5 + ["DOWN_10"] * 5 + ["NO_EVENT"] * (rows - 10),
            "return_1m": [0.03] * 5 + [-0.03] * 5 + [0.0] * (rows - 10),
            "volatility_5m": [0.02] * 10 + [0.001] * (rows - 10),
        }
    )

    model, report = train_multinomial_baseline(
        frame,
        ["return_1m", "volatility_5m"],
        BaselineConfig(minimum_rows=500),
    )

    assert model is None
    assert report.status == "WAIT"
    assert report.reason == "insufficient_directional_event_test_support"


def test_ambiguous_and_incomplete_are_excluded() -> None:
    frame = _dataset(600)
    frame.loc[0, "label"] = "AMBIGUOUS"
    frame.loc[1, "label"] = "INCOMPLETE"
    model, report = train_multinomial_baseline(
        frame,
        ["return_1m", "volatility_5m"],
        BaselineConfig(minimum_rows=500),
    )
    assert model is not None
    assert report.row_count == 598
