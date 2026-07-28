"""Canonical time identity for completed Binance one-minute candles."""

from __future__ import annotations

MINUTE_MS = 60_000


def canonical_kline_availability_timestamp(
    *,
    open_time_ms: int,
    close_time_ms: int,
    interval_ms: int = MINUTE_MS,
) -> int:
    """Return the conservative availability boundary derived from candle identity.

    Binance normally reports ``closeTime = openTime + interval - 1``. During an
    exchange halt it can close the last candle early, so ``closeTime`` is not a
    stable candle identifier. ``openTime`` is stable; the nominal interval end
    is also a conservative point-in-time availability boundary.
    """

    if interval_ms <= 0:
        raise ValueError("kline interval must be positive")
    if open_time_ms < 0 or close_time_ms < 0:
        raise ValueError("kline timestamps must be non-negative")
    if open_time_ms % interval_ms != 0:
        raise ValueError("kline openTime must align to its interval")
    nominal_close_time_ms = open_time_ms + interval_ms - 1
    if close_time_ms < open_time_ms or close_time_ms > nominal_close_time_ms:
        raise ValueError(
            "kline closeTime must fall inside its openTime interval: "
            f"open_time_ms={open_time_ms}, close_time_ms={close_time_ms}"
        )
    return open_time_ms + interval_ms


__all__ = ["MINUTE_MS", "canonical_kline_availability_timestamp"]
