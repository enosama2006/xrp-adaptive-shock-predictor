"""Explicit, fail-closed registry for model-eligible feature columns.

New numeric columns must not silently enter training.  A feature is eligible only
when its exact name or prefix is approved here.  Raw prices, raw quantities,
far-book context, diagnostics, targets, identifiers, and availability timestamps
are always excluded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

SCHEMA_VERSION = "market-features-v3-explicit-registry"

# Exact model inputs whose semantics are scale-stable and point-in-time causal.
_ALLOWED_EXACT = {
    "book_spread_bps",
    "best_level_imbalance",
    "microprice_deviation_bps",
    "distance_weighted_imbalance",
    "log1p_volume",
    "log1p_quote_volume",
    "taker_buy_ratio",
    "signed_volume_ratio",
    "average_trade_size_log1p",
    "trade_intensity_log1p",
}

_ALLOWED_PREFIXES = (
    "return_",
    "log_return_",
    "volatility_",
    "volatility_of_volatility_",
    "range_position_",
    "drawdown_",
    "distance_from_low_",
    "distance_from_high_",
    "price_zscore_",
    "return_robust_zscore_",
    "momentum_",
    "jump_score_",
    "volume_zscore_",
    "volume_robust_zscore_",
    "quote_volume_zscore_",
    "quote_volume_robust_zscore_",
    "taker_buy_ratio_zscore_",
    "signed_volume_zscore_",
    "trade_intensity_zscore_",
    "average_trade_size_zscore_",
    "depth_imbalance_5bps",
    "depth_imbalance_10bps",
    "depth_imbalance_25bps",
    "depth_imbalance_50bps",
    "depth_imbalance_100bps",
    "depth_imbalance_200bps",
    "log_bid_depth_5bps",
    "log_bid_depth_10bps",
    "log_bid_depth_25bps",
    "log_bid_depth_50bps",
    "log_bid_depth_100bps",
    "log_bid_depth_200bps",
    "log_ask_depth_5bps",
    "log_ask_depth_10bps",
    "log_ask_depth_25bps",
    "log_ask_depth_50bps",
    "log_ask_depth_100bps",
    "log_ask_depth_200bps",
    "log_ask_to_bid_pressure_5bps",
    "log_ask_to_bid_pressure_10bps",
    "log_ask_to_bid_pressure_25bps",
    "log_ask_to_bid_pressure_50bps",
    "log_ask_to_bid_pressure_100bps",
    "log_ask_to_bid_pressure_200bps",
    "ofi_",
    "cvd_",
    "book_depletion_",
    "book_replenishment_",
    "book_cancellation_",
    "book_persistence_",
    "btc_",
    "eth_",
    "funding_",
    "open_interest_",
    "basis_",
    "liquidation_",
    "availability_",
)

# These columns may exist for audit, display, targets, or context, but can never
# be selected automatically as model inputs.
_PROHIBITED_EXACT = {
    "timestamp_ms",
    "feature_available_at_ms",
    "price",
    "open",
    "high",
    "low",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "book_mid_price",
    "microprice",
    "book_total_bid_qty",
    "book_total_ask_qty",
    "anchor_timestamp_ms",
    "anchor_price",
    "horizon_minutes",
    "horizon_end_ms",
    "upper_barrier_price",
    "lower_barrier_price",
    "max_price",
    "min_price",
    "max_return",
    "min_return",
    "touch_timestamp_ms",
    "touch_price",
}

_PROHIBITED_PREFIXES = (
    "context_",
    "diagnostic_",
    "outer_",
    "raw_",
    "target_",
    "future_",
    "bid_depth_",
    "ask_depth_",
    "nearest_bid_wall_qty",
    "nearest_ask_wall_qty",
)


@dataclass(frozen=True, slots=True)
class FeatureSelectionAudit:
    schema_version: str
    eligible: tuple[str, ...]
    excluded_unknown_numeric: tuple[str, ...]
    prohibited_present: tuple[str, ...]
    non_numeric: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "eligible": list(self.eligible),
            "excluded_unknown_numeric": list(self.excluded_unknown_numeric),
            "prohibited_present": list(self.prohibited_present),
            "non_numeric": list(self.non_numeric),
        }


def is_prohibited_feature(name: str) -> bool:
    return name in _PROHIBITED_EXACT or name.startswith(_PROHIBITED_PREFIXES)


def is_model_feature(name: str) -> bool:
    if is_prohibited_feature(name):
        return False
    return name in _ALLOWED_EXACT or name.startswith(_ALLOWED_PREFIXES)


def audit_feature_columns(frame: pd.DataFrame) -> FeatureSelectionAudit:
    eligible: list[str] = []
    excluded_unknown: list[str] = []
    prohibited: list[str] = []
    non_numeric: list[str] = []

    for name in frame.columns:
        if is_prohibited_feature(name):
            prohibited.append(name)
            continue
        if not pd.api.types.is_numeric_dtype(frame[name]):
            non_numeric.append(name)
            continue
        if is_model_feature(name):
            eligible.append(name)
        else:
            excluded_unknown.append(name)

    return FeatureSelectionAudit(
        schema_version=SCHEMA_VERSION,
        eligible=tuple(eligible),
        excluded_unknown_numeric=tuple(excluded_unknown),
        prohibited_present=tuple(prohibited),
        non_numeric=tuple(non_numeric),
    )


def select_model_feature_names(frame: pd.DataFrame) -> list[str]:
    """Return explicitly approved numeric features in deterministic column order."""

    audit = audit_feature_columns(frame)
    if not audit.eligible:
        raise ValueError("no registered model features are available")
    return list(audit.eligible)
