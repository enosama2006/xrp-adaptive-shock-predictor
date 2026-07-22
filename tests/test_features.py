import pandas as pd
import pytest

from xasp.features import build_price_features, join_anchors_with_features


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
    assert result.loc[2, "return_1m"] == pytest.approx((1.02 / 1.01) - 1)


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
