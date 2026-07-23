"""Memory-bounded extended-horizon Model B training."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

import joblib
import pandas as pd

from .anchor_dataset import AnchorDatasetStore
from .baseline import BaselineConfig
from .extended_runtime import HORIZONS, ExtendedHorizonRealDataPlatform
from .feature_registry import SCHEMA_VERSION as FEATURE_SCHEMA_VERSION
from .features import join_anchors_with_features
from .first_touch_v4 import FIRST_TOUCH_GATE_VERSION, train_first_touch_v4
from .horizons import DAILY_FINALIZED_HORIZON_ROWS, RESEARCH_HORIZON_SET_VERSION


class MemorySafeExtendedHorizonPlatform(ExtendedHorizonRealDataPlatform):
    """Join and release one horizon matrix at a time during Model B training."""

    def train_if_due(self, force: bool = False) -> bool:
        if not self.config.training_enabled:
            return False
        anchor_store = AnchorDatasetStore(self.paths.anchors)
        anchor_stats = anchor_store.stats()
        features = pd.read_parquet(self.paths.features)
        final_count = anchor_stats.final_rows
        retrain_rows = max(
            self.config.retrain_after_new_final_rows,
            DAILY_FINALIZED_HORIZON_ROWS,
        )
        due = force or (final_count >= self.status.last_training_final_rows + retrain_rows)
        if not due:
            return False

        self._set_lifecycle(
            "TRAIN_MODEL_B",
            progress=0.0,
            message="training_first_touch_independent_horizon_challengers",
        )
        self._save_feature_diagnostics(features)
        feature_names = self._feature_names(features)
        incumbent_models: dict[int, Any] = {}
        if self._bundle is not None:
            incumbent_models = {
                int(horizon): model for horizon, model in self._bundle.get("models", {}).items()
            }
        models = dict(incumbent_models)
        reports: dict[str, Any] = {}
        promoted_horizons: list[int] = []
        rejected_horizons: list[int] = []

        for index, horizon in enumerate(HORIZONS, start=1):
            anchor_subset = anchor_store.load(
                horizons=(horizon,),
                statuses=("FINAL",),
            )
            subset = join_anchors_with_features(anchor_subset, features)
            horizon_ms = horizon * 60_000
            model, report = train_first_touch_v4(
                subset,
                feature_names,
                BaselineConfig(
                    minimum_rows=self.config.minimum_final_rows_per_horizon,
                    label_horizon_ms=horizon_ms,
                    embargo_ms=horizon_ms,
                ),
            )
            reports[str(horizon)] = asdict(report)
            if model is None:
                rejected_horizons.append(horizon)
            else:
                models[horizon] = model
                promoted_horizons.append(horizon)
            del anchor_subset, subset
            self._set_lifecycle(
                "TRAIN_MODEL_B",
                progress=index / len(HORIZONS),
                message=f"trained_or_evaluated_horizon_{horizon}m",
            )

        self.paths.reports.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = self.paths.reports.with_suffix(".json.tmp")
        temporary_report.write_text(
            json.dumps(reports, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_report.replace(self.paths.reports)
        self.status.last_training_final_rows = final_count

        if not models:
            self._bundle = None
            self.status.model_available = False
            self.status.model_version = None
            self.status.state = "WAIT"
            self.status.reason = self._failure_reason(reports)
            self._set_lifecycle(
                "MODEL_B_WAIT",
                progress=1.0,
                message="model_b_all_horizons_wait",
            )
            return False

        if not promoted_horizons and self._bundle is not None:
            self.status.model_available = True
            self.status.model_version = str(self._bundle["model_version"])
            self.status.state = "PARTIAL_RESEARCH"
            self.status.reason = "model_b_all_challengers_rejected_champion_retained"
            self._set_lifecycle(
                "MODEL_B_WAIT",
                progress=1.0,
                message="model_b_challengers_rejected_existing_horizons_retained",
            )
            return False

        version = f"real-logistic-independent-horizons-{int(time.time())}"
        bundle: dict[str, Any] = {
            "model_version": version,
            "trained_at_ms": int(time.time() * 1000),
            "feature_names": feature_names,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "gate_methodology_version": FIRST_TOUCH_GATE_VERSION,
            "horizon_set_version": RESEARCH_HORIZON_SET_VERSION,
            "configured_horizons": list(HORIZONS),
            "available_horizons": sorted(models),
            "promoted_horizons_this_run": promoted_horizons,
            "rejected_horizons_this_run": rejected_horizons,
            "models": models,
            "reports": reports,
            "training_final_rows": final_count,
            "source": "real_binance_public_data_only",
            "promotion_evidence": (
                "independent_event_clusters_plus_purged_walk_forward_support_"
                "plus_untouched_temporal_precision"
            ),
            "promoted_for_trading": False,
        }
        self.paths.models.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.models.with_suffix(".joblib.tmp")
        joblib.dump(bundle, temporary)
        temporary.replace(self.paths.models)
        self._bundle = bundle
        self.status.model_available = True
        self.status.model_version = version
        self.status.state = "PARTIAL_RESEARCH"
        self.status.reason = (
            "model_b_all_horizons_research_ready"
            if set(models) == set(HORIZONS)
            else "model_b_some_horizons_research_ready_others_wait"
        )
        self._set_lifecycle(
            "MODEL_B_RESEARCH_READY",
            progress=1.0,
            message="model_b_independent_horizon_gates_evaluated",
        )
        return True


__all__ = ["MemorySafeExtendedHorizonPlatform"]
