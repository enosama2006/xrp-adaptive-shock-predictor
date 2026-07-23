"""XASP: governed dual-model shock prediction research toolkit."""

from . import anchor_dataset as _anchor_dataset
from . import future_envelope as _future_envelope
from .fast_anchor_dataset import update_anchor_dataset_from_candles_fast
from .fast_future_envelope import build_future_envelope_targets_fast
from .labeling import BarrierConfig, BarrierLabel, label_first_touch

# Transitional routing while the refactor keeps public import paths stable.
# Pipeline and envelope modules import these symbols after package initialization,
# so production bootstrap uses the vectorized implementations without breaking
# existing callers or persisted schemas.
_anchor_dataset.update_anchor_dataset_from_candles = update_anchor_dataset_from_candles_fast
_future_envelope.build_future_envelope_targets = build_future_envelope_targets_fast

__all__ = ["BarrierConfig", "BarrierLabel", "label_first_touch"]
