"""Order-book features that prioritize executable liquidity near the current price."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Iterable


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float
    quantity: float

    def __post_init__(self) -> None:
        if self.price <= 0 or self.quantity < 0:
            raise ValueError("book levels require positive price and non-negative quantity")


@dataclass(frozen=True, slots=True)
class ProximityConfig:
    # 5 bps = 0.05%; 1000 bps = 10%, matching the prediction barrier.
    # A 50% band is intentionally excluded from primary signals because it is far
    # outside the 15-60 minute decision horizon and can be dominated by stale walls.
    bands_bps: tuple[int, ...] = (5, 10, 25, 50, 100, 200, 500, 1000)
    decay_bps: float = 50.0
    outer_context_bps: int = 2000

    def __post_init__(self) -> None:
        if not self.bands_bps or any(value <= 0 for value in self.bands_bps):
            raise ValueError("bands_bps must contain positive values")
        if tuple(sorted(set(self.bands_bps))) != self.bands_bps:
            raise ValueError("bands_bps must be strictly increasing and unique")
        if self.decay_bps <= 0:
            raise ValueError("decay_bps must be positive")
        if self.outer_context_bps < self.bands_bps[-1]:
            raise ValueError("outer_context_bps must cover the widest primary band")


def _distance_bps(level_price: float, mid_price: float) -> float:
    return abs(level_price - mid_price) / mid_price * 10_000.0


def _depth_within(levels: Iterable[BookLevel], mid_price: float, band_bps: int) -> float:
    return sum(
        level.quantity
        for level in levels
        if _distance_bps(level.price, mid_price) <= band_bps
    )


def _distance_weighted_depth(
    levels: Iterable[BookLevel],
    mid_price: float,
    decay_bps: float,
) -> float:
    return sum(
        level.quantity * exp(-_distance_bps(level.price, mid_price) / decay_bps)
        for level in levels
    )


def _nearest_wall(levels: list[BookLevel], mid_price: float) -> tuple[float | None, float | None]:
    positive = [level for level in levels if level.quantity > 0]
    if not positive:
        return None, None
    nearest = min(positive, key=lambda level: _distance_bps(level.price, mid_price))
    return _distance_bps(nearest.price, mid_price), nearest.quantity


def build_proximity_features(
    *,
    bids: Iterable[BookLevel],
    asks: Iterable[BookLevel],
    best_bid: float,
    best_ask: float,
    config: ProximityConfig = ProximityConfig(),
) -> dict[str, float | None]:
    """Build liquidity features using distance from the tradable mid-price.

    Primary signals use narrow executable bands and the full ±10% target path.
    Wider depth is retained only as context, never as the dominant imbalance signal.
    """

    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        raise ValueError("invalid best bid/ask")
    bid_levels = list(bids)
    ask_levels = list(asks)
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 10_000.0

    features: dict[str, float | None] = {
        "book_mid_price": mid,
        "book_spread_bps": spread_bps,
        "book_total_bid_qty": sum(level.quantity for level in bid_levels),
        "book_total_ask_qty": sum(level.quantity for level in ask_levels),
    }

    for band in config.bands_bps:
        bid_depth = _depth_within(bid_levels, mid, band)
        ask_depth = _depth_within(ask_levels, mid, band)
        denominator = bid_depth + ask_depth
        features[f"bid_depth_{band}bps"] = bid_depth
        features[f"ask_depth_{band}bps"] = ask_depth
        features[f"depth_imbalance_{band}bps"] = (
            0.0 if denominator == 0 else (bid_depth - ask_depth) / denominator
        )
        features[f"ask_to_bid_pressure_{band}bps"] = (
            None if bid_depth == 0 else ask_depth / bid_depth
        )

    outer_bid = _depth_within(bid_levels, mid, config.outer_context_bps)
    outer_ask = _depth_within(ask_levels, mid, config.outer_context_bps)
    outer_total = outer_bid + outer_ask
    features[f"outer_bid_depth_{config.outer_context_bps}bps"] = outer_bid
    features[f"outer_ask_depth_{config.outer_context_bps}bps"] = outer_ask
    features[f"outer_imbalance_{config.outer_context_bps}bps"] = (
        0.0 if outer_total == 0 else (outer_bid - outer_ask) / outer_total
    )

    weighted_bid = _distance_weighted_depth(bid_levels, mid, config.decay_bps)
    weighted_ask = _distance_weighted_depth(ask_levels, mid, config.decay_bps)
    weighted_total = weighted_bid + weighted_ask
    features["distance_weighted_bid_depth"] = weighted_bid
    features["distance_weighted_ask_depth"] = weighted_ask
    features["distance_weighted_imbalance"] = (
        0.0 if weighted_total == 0 else (weighted_bid - weighted_ask) / weighted_total
    )

    bid_wall_distance, bid_wall_qty = _nearest_wall(bid_levels, mid)
    ask_wall_distance, ask_wall_qty = _nearest_wall(ask_levels, mid)
    features["nearest_bid_wall_distance_bps"] = bid_wall_distance
    features["nearest_bid_wall_qty"] = bid_wall_qty
    features["nearest_ask_wall_distance_bps"] = ask_wall_distance
    features["nearest_ask_wall_qty"] = ask_wall_qty

    return features
