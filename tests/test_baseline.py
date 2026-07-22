import pandas as pd

from xasp.baseline import BaselineConfig, train_multinomial_baseline


def _dataset(rows: int) -> pd.DataFrame:
    labels = ["UP_10", "DOWN_10", "NO_EVENT"]
    return pd.DataFrame(
        {
            "anchor_timestamp_ms": [i * 60_000 for i in range(rows)],
            "status": ["FINAL"] * rows,
            "label": [labels[i % 3] for i in range(rows)],
            "return_1m": [((i % 11) - 5) / 1000 for i in range(rows)],
            "volatility_5m": [0.001 + (i % 7) / 10000 for i in range(rows)],
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


def test_temporal_baseline_trains_without_promotion() -> None:
    model, report = train_multinomial_baseline(
        _dataset(600),
        ["return_1m", "volatility_5m"],
        BaselineConfig(minimum_rows=500),
    )
    assert model is not None
    assert report.status == "RESEARCH_ONLY"
    assert report.train_rows < report.row_count
    assert report.test_rows > 0
    assert report.metrics["probability_sum_max_error"] < 1e-9
    assert set(report.metrics["per_class"]) == {"UP_10", "DOWN_10", "NO_EVENT"}


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
