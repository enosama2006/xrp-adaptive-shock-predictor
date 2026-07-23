"""Governed research horizons shared by both independent models."""

from __future__ import annotations

RESEARCH_HORIZONS_MINUTES: tuple[int, ...] = (
    15,
    30,
    45,
    60,
    120,
    180,
    240,
    480,
)
RESEARCH_HORIZON_KEYS: tuple[str, ...] = tuple(
    str(value) for value in RESEARCH_HORIZONS_MINUTES
)
RESEARCH_HORIZON_SET_VERSION = (
    "xasp-horizons-15-30-45-60-120-180-240-480-v1"
)
MAX_RESEARCH_HORIZON_MINUTES = max(RESEARCH_HORIZONS_MINUTES)
MINUTES_PER_DAY = 1_440
DAILY_FINALIZED_HORIZON_ROWS = MINUTES_PER_DAY * len(RESEARCH_HORIZONS_MINUTES)


__all__ = [
    "DAILY_FINALIZED_HORIZON_ROWS",
    "MAX_RESEARCH_HORIZON_MINUTES",
    "MINUTES_PER_DAY",
    "RESEARCH_HORIZON_KEYS",
    "RESEARCH_HORIZONS_MINUTES",
    "RESEARCH_HORIZON_SET_VERSION",
]
