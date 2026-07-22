from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from xasp.feature_registry import select_model_feature_names
from xasp.features import build_price_features


def _frame(rows: int = 300) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    volume = 100.0 + (index % 20)
    taker_buy = volume * (0.35 + (index % 10) / 100.0)
    return pd.DataFrame(
        {
            "timestamp_ms": np.arange(rows, dtype=np.int64) * 60_000,
            "price": 1.0 + index / 100_000.0,
            "volume": volume,
            "quote_volume": volume * (1.0 + index / 100_000.0),
            "trade_count": 20 + (index % 7),
            "taker_buy_base": taker_buy,
            "taker_buy_quote": taker_buy * (1.0 + index / 100_000.0),
        }
    )


def test_trade_flow_fields_are_transformed_and_raw_values_removed() -> None:
    features = build_price_features(_frame())
    selected = select_model_feature_names(features)

    assert "volume" not in features.columns
    assert "quote_volume" not in features.columns
    assert "trade_count" not in features.columns
    assert "taker_buy_base" not in features.columns
    assert "log1p_volume" in selected
    assert "log1p_quote_volume" in selected
    assert "taker_buy_ratio" in selected
    assert "signed_volume_ratio" in selected
    assert "trade_intensity_log1p" in selected
    assert "average_trade_size_log1p" in selected
    assert features["taker_buy_ratio"].between(0, 1).all()


def test_taker_buy_volume_cannot_exceed_total_volume() -> None:
    frame = _frame(20)
    frame.loc[5, "taker_buy_base"] = frame.loc[5, "volume"] + 1

    with pytest.raises(ValueError, match="cannot exceed"):
        build_price_features(frame)
