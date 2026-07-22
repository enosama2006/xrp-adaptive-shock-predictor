"""Prediction cadence policy for rolling multi-horizon forecasts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CadencePolicy:
    """Separates raw-data frequency, feature refresh, and official snapshots."""

    feature_refresh_ms: int = 5_000
    prediction_cadence_ms: int = 60_000
    horizons_minutes: tuple[int, ...] = (15, 30, 45, 60)

    def __post_init__(self) -> None:
        if self.feature_refresh_ms <= 0:
            raise ValueError("feature_refresh_ms must be positive")
        if self.prediction_cadence_ms <= 0:
            raise ValueError("prediction_cadence_ms must be positive")
        if self.feature_refresh_ms > self.prediction_cadence_ms:
            raise ValueError("feature refresh cannot be slower than official prediction cadence")
        if not self.horizons_minutes or any(value <= 0 for value in self.horizons_minutes):
            raise ValueError("horizons_minutes must contain positive values")

    def official_anchor_timestamp_ms(self, observed_timestamp_ms: int) -> int:
        """Floor an observed timestamp to the latest completed official cadence boundary."""
        if observed_timestamp_ms < 0:
            raise ValueError("observed_timestamp_ms must be non-negative")
        return (observed_timestamp_ms // self.prediction_cadence_ms) * self.prediction_cadence_ms

    def next_official_anchor_ms(self, last_anchor_ms: int | None, now_ms: int) -> int | None:
        """Return the next due anchor, or None when no official snapshot is due yet."""
        if now_ms < 0:
            raise ValueError("now_ms must be non-negative")
        latest_completed = self.official_anchor_timestamp_ms(now_ms)
        if last_anchor_ms is None:
            return latest_completed
        if last_anchor_ms < 0:
            raise ValueError("last_anchor_ms must be non-negative")
        candidate = last_anchor_ms + self.prediction_cadence_ms
        return candidate if candidate <= latest_completed else None

    def horizon_end_ms(self, anchor_timestamp_ms: int, horizon_minutes: int) -> int:
        if anchor_timestamp_ms < 0:
            raise ValueError("anchor_timestamp_ms must be non-negative")
        if horizon_minutes not in self.horizons_minutes:
            raise ValueError("horizon_minutes is not approved by this policy")
        return anchor_timestamp_ms + horizon_minutes * 60_000
