"""Order-book features that prioritize executable liquidity near the current price.

Primary model signals are restricted to near-price bands. Medium/far depth is
emitted only with explicit context/diagnostic prefixes, which the feature registry
excludes from model training by default. A far-away wall therefore cannot flip
near-price pressure or distance-weighted imbalance.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log1p
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
    # Primary executable-liquidity bands: 0.05% through 2%.
    primary_bands_bps: tuple[int, ...] = (5, 10, 25, 50, 100, 200)
    # Medium and target-corridor context. These are not model eligible by default.
    context_bands_bps: tuple[int, ...] = (500, 1000)
    # 20% diagnostic context only. 50% and farther are intentionally omitted.
    diagnostic_band_bps: int = 2000
    decay_bps: float = 50.0

    def __post_init__(self) -> None:
        for name, values in (
            ("primary_bands_bps", self.primary_bands_bps),
            ("context_bands_bps", self.context_bands_bps),
        ):
            if not values or any(value <= 0 for value in values):
                raise ValueError(f"{name} must contain positive values")
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{name} must be strictly increasing and unique")
        if self.primary_bands_bps[-1] >= self.context_bands_bps[0]:
            raise ValueError("primary bands must be narrower than context bands")
        if self.diagnostic_band_bps <= self.context_bands_bps[-1]:
            raise ValueError("diagnostic band must be wider than context bands")
        if self.decay_bps <= 0:
            raise ValueError("decay_bps must be positive")

    @property
    def bands_bps(self) -> tuple[int, ...]:
        """Compatibility view of all non-diagnostic bands."""

        return self.primary_bands_bps + self.context_bands_bps


FeatureValue = float | None


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
    maximum_distance_bps: int,
) -> float:
    """Weight only executable near depth; far levels have exactly zero influence."""

    return sum(
        level.quantity * exp(-_distance_bps(level.price, mid_price) / decay_bps)
        for level in levels
        if _distance_bps(level.price, mid_price) <= maximum_distance_bps
    )


def _nearest_quantity(levels: list[BookLevel], reference_price: float) -> float:
    positive = [level for level in levels if level.quantity > 0]
    if not positive:
        return 0.0
    return min(positive, key=lambda level: abs(level.price - reference_price)).quantity


def _imbalance(bid_depth: float, ask_depth: float) -> float:
    total = bid_depth + ask_depth
    return 0.0 if total == 0 else (bid_depth - ask_depth) / total


def _add_band_features(
    features: dict[str, FeatureValue],
    *,
    prefix: str,
    band: int,
    bid_depth: float,
    ask_depth: float,
) -> None:
    features[f"{prefix}log_bid_depth_{band}bps"] = log1p(bid_depth)
    features[f"{prefix}log_ask_depth_{band}bps"] = log1p(ask_depth)
    features[f"{prefix}depth_imbalance_{band}bps"] = _imbalance(bid_depth, ask_depth)
    features[f"{prefix}log_ask_to_bid_pressure_{band}bps"] = (
        log1p(ask_depth) - log1p(bid_depth)
    )


def build_proximity_features(
    *,
    bids: Iterable[BookLevel],
    asks: Iterable[BookLevel],
    best_bid: float,
    best_ask: float,
    config: ProximityConfig = ProximityConfig(),
) -> dict[str, FeatureValue]:
    """Build model-safe near-book signals plus explicitly isolated context.

    The returned primary features use only bands up to 2% and distance weighting
    truncated at the widest primary band. Context features at 5%, 10%, and 20%
    are named with prefixes that keep them out of training unless a later,
    reviewed feature-schema version explicitly promotes them.
    """

    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        raise ValueError("invalid best bid/ask")

    bid_levels = list(bids)
    ask_levels = list(asks)
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 10_000.0

    best_bid_qty = _nearest_quantity(bid_levels, best_bid)
    best_ask_qty = _nearest_quantity(ask_levels, best_ask)
    best_total = best_bid_qty + best_ask_qty
    microprice = (
        None
        if best_total == 0
        else (best_ask * best_bid_qty + best_bid * best_ask_qty) / best_total
    )

    features: dict[str, FeatureValue] = {
        "book_mid_price": mid,
        "book_spread_bps": spread_bps,
        "best_level_imbalance": _imbalance(best_bid_qty, best_ask_qty),
        "microprice": microprice,
        "microprice_deviation_bps": (
            None if microprice is None else (microprice - mid) / mid * 10_000.0
        ),
        # Log quantities are audit/context fields. The explicit registry decides
        # whether a future schema may use them.
        "context_best_bid_qty_log1p": log1p(best_bid_qty),
        "context_best_ask_qty_log1p": log1p(best_ask_qty),
    }

    for band in config.primary_bands_bps:
        _add_band_features(
            features,
            prefix="",
            band=band,
            bid_depth=_depth_within(bid_levels, mid, band),
            ask_depth=_depth_within(ask_levels, mid, band),
        )

    for band in config.context_bands_bps:
        _add_band_features(
            features,
            prefix="context_",
            band=band,
            bid_depth=_depth_within(bid_levels, mid, band),
            ask_depth=_depth_within(ask_levels, mid, band),
        )

    diagnostic_band = config.diagnostic_band_bps
    _add_band_features(
        features,
        prefix="diagnostic_",
        band=diagnostic_band,
        bid_depth=_depth_within(bid_levels, mid, diagnostic_band),
        ask_depth=_depth_within(ask_levels, mid, diagnostic_band),
    )

    maximum_model_distance = config.primary_bands_bps[-1]
    weighted_bid = _distance_weighted_depth(
        bid_levels,
        mid,
        config.decay_bps,
        maximum_model_distance,
    )
    weighted_ask = _distance_weighted_depth(
        ask_levels,
        mid,
        config.decay_bps,
        maximum_model_distance,
    )
    features["distance_weighted_imbalance"] = _imbalance(weighted_bid, weighted_ask)
    features["context_distance_weighted_bid_depth_log1p"] = log1p(weighted_bid)
    features["context_distance_weighted_ask_depth_log1p"] = log1p(weighted_ask)

    return features
