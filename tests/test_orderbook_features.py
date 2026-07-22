from __future__ import annotations

import pytest

from xasp.feature_registry import audit_feature_columns, select_model_feature_names
from xasp.orderbook_features import BookLevel, build_proximity_features


def _base_book() -> tuple[list[BookLevel], list[BookLevel]]:
    bids = [
        BookLevel(price=99.99, quantity=120.0),
        BookLevel(price=99.90, quantity=80.0),
        BookLevel(price=99.50, quantity=25.0),
    ]
    asks = [
        BookLevel(price=100.01, quantity=60.0),
        BookLevel(price=100.10, quantity=40.0),
        BookLevel(price=100.50, quantity=25.0),
    ]
    return bids, asks


def test_huge_far_bid_cannot_flip_primary_pressure() -> None:
    bids, asks = _base_book()
    baseline = build_proximity_features(
        bids=bids,
        asks=asks,
        best_bid=99.99,
        best_ask=100.01,
    )
    with_far_wall = build_proximity_features(
        bids=[*bids, BookLevel(price=50.0, quantity=10**30)],
        asks=asks,
        best_bid=99.99,
        best_ask=100.01,
    )

    primary_names = [
        "best_level_imbalance",
        "microprice_deviation_bps",
        "distance_weighted_imbalance",
        "depth_imbalance_5bps",
        "depth_imbalance_10bps",
        "depth_imbalance_25bps",
        "depth_imbalance_50bps",
        "depth_imbalance_100bps",
        "depth_imbalance_200bps",
    ]
    for name in primary_names:
        assert with_far_wall[name] == pytest.approx(baseline[name])


def test_context_and_diagnostic_book_fields_are_not_model_eligible() -> None:
    import pandas as pd

    bids, asks = _base_book()
    features = build_proximity_features(
        bids=bids,
        asks=asks,
        best_bid=99.99,
        best_ask=100.01,
    )
    frame = pd.DataFrame([features])
    selected = select_model_feature_names(frame)
    audit = audit_feature_columns(frame)

    assert "depth_imbalance_200bps" in selected
    assert "context_depth_imbalance_500bps" not in selected
    assert "context_depth_imbalance_1000bps" not in selected
    assert "diagnostic_depth_imbalance_2000bps" not in selected
    assert "book_mid_price" not in selected
    assert "microprice" not in selected
    assert "context_depth_imbalance_500bps" in audit.prohibited_present


def test_microprice_moves_toward_side_with_more_best_level_size() -> None:
    features = build_proximity_features(
        bids=[BookLevel(price=99.99, quantity=200.0)],
        asks=[BookLevel(price=100.01, quantity=50.0)],
        best_bid=99.99,
        best_ask=100.01,
    )

    assert features["best_level_imbalance"] == pytest.approx(0.6)
    assert features["microprice"] is not None
    assert float(features["microprice"]) > 100.0
    assert float(features["microprice_deviation_bps"]) > 0
