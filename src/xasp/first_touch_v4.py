"""Compatibility alias for the stricter Model B v5 gate.

Runtime modules that previously imported ``first_touch_v4`` now receive the
independent-event and independent-period performance methodology. The old v4
implementation is intentionally not used for new bundles.
"""

from __future__ import annotations

from .first_touch_v5 import (
    FIRST_TOUCH_GATE_VERSION,
    MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS,
    train_first_touch_v5,
)

train_first_touch_v4 = train_first_touch_v5


__all__ = [
    "FIRST_TOUCH_GATE_VERSION",
    "MINIMUM_INDEPENDENT_EVENT_CLUSTERS_PER_CLASS",
    "train_first_touch_v4",
]
