import numpy as np
import pandas as pd
import pytest

from xasp.features import (
    build_feature_diagnostics,
    build_price_features,
    join_anchors_with_features,
)


def test_price_features_are_causal_and_sorted() -> None:
    prices = pd.DataFrame(
        {
            "timestamp_ms": [120_000, 0, 60_000],
            "price": [1.02, 1.0, 1.01],
        }
    )
    result = build_price_features(prices)
    assert result["timestamp_ms"].tolist() == [0, 60_000, 120_000]
    assert result.loc[0, "feature_available_at_ms"] == 0
    assert pd.isna(result.loc[0, "return_1m"])
    assert pd.isna(result.loc[0, "log_return_1m"])
    assert result.loc[2, "return_1m"] == pytest.approx((1.02 / 1.01) - 1)
    assert result.loc[2, "log_return_1m"] == pytest.approx(np.log(1.02 / 1.01))


def test_full_price_precision_is_retained_but_not_required_as_model_scale() -> None:
    prices = pd.DataFrame(
        {
            "timestamp_ms": [0, 60_000, 120_000],
            "price": [1.255458, 1.254478, 1.354660],
        }
    )
    result = build_price_features(prices)
    assert result["price"].tolist() == pytest.approx(prices["price"].tolist())
    assert result.loc[2, "return_1m"] == pytest.approx(1.354660 / 1.254478 - 1)


def test_volume_is_log_compressed_and_raw_volume_is_not_a_model_feature() -> None:
    prices = pd.DataFrame(
        {
            "timestamp_ms": np.arange(300, dtype=np.int64) * 60_000,
            "price": 1.0 + np.arange(300) * 0.0001,
            "volume": np.geomspace(1.0, 1_000_000.0, 300),
        }
    )
    result = build_price_features(prices)
    assert "volume" not in result.columns
    assert "log1p_volume" in result.columns
    assert "volume_robust_zscore_60m" in result.columns
    assert np.isfinite(result["log1p_volume"]).all()


def test_feature_diagnostics_include_distribution_and_histogram() -> None:
    prices = pd.DataFrame(
        {
            "timestamp_ms": np.arange(100, dtype=np.int64) * 60_000,
            "price": 1.0 + np.sin(np.arange(100) / 10.0) * 0.01,
        }
    )
    features = build_price_features(prices)
    report = build_feature_diagnostics(features, histogram_bins=10)
    item = report["features"]["log_return_1m"]
    assert item["status"] == "OK"
    assert len(item["histogram"]["counts"]) == 10
    assert len(item["histogram"]["edges"]) == 11
    assert "skewness" in item


def test_join_rejects_future_feature_availability() -> None:
    anchors = pd.DataFrame({"anchor_timestamp_ms": [60_000]})
    features = pd.DataFrame(
        {
            "timestamp_ms": [60_000],
            "price": [1.0],
            "feature_available_at_ms": [120_000],
        }
    )
    with pytest.raises(ValueError, match="availability"):
        join_anchors_with_features(anchors, features)


def test_non_positive_price_is_rejected() -> None:
    frame = pd.DataFrame({"timestamp_ms": [0], "price": [0.0]})
    with pytest.raises(ValueError, match="positive"):
        build_price_features(frame)
