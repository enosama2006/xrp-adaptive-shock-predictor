"""Memory-bounded joint first-touch runtime.

The joint competing-risk trainer needs only the completed eight-hour partition:
the recorded first-touch timestamp determines the event hour directly.  It does
not materialize an eight-times-larger joined horizon matrix.
"""

from __future__ import annotations

from .extended_runtime import ExtendedHorizonRealDataPlatform


class MemorySafeExtendedHorizonPlatform(ExtendedHorizonRealDataPlatform):
    """Compatibility name for the now inherently memory-bounded joint runtime."""


__all__ = ["MemorySafeExtendedHorizonPlatform"]
