from __future__ import annotations

from pathlib import Path

import pandas as pd

from xasp.first_passage_discovery import (
    DiscoveryConfig,
    build_first_passage_discovery,
    generate_discovery_report,
)
from xasp.price_store import PartitionedPriceStore

MINUTE_MS = 60_000


def _prices(rows: int = 500) -> pd.DataFrame:
    timestamps = [index * MINUTE_MS for index in range(rows)]
    frame = pd.DataFrame(
        {
            "timestamp_ms": timestamps,
            "price": [100.0] * rows,
            "open": [100.0] * rows,
            "high": [100.5] * rows,
            "low": [99.5] * rows,
            "volume": [1_000.0 + index for index in range(rows)],
        }
    )
    for index in (80, 180, 280, 380):
        frame.loc[index, "high"] = 111.0
    for index in (130, 230, 330, 430):
        frame.loc[index, "low"] = 89.0
    return frame


def _config() -> DiscoveryConfig:
    return DiscoveryConfig(
        horizons_minutes=(5, 10, 20, 40),
        threshold_return=0.10,
        anchor_stride_minutes=5,
        volatility_window_minutes=20,
        batch_rows=8,
        independent_cluster_separation_minutes=15,
    )


def test_discovery_reports_observed_passage_times_and_excursions() -> None:
    report = build_first_passage_discovery(_prices(), _config())

    assert report["status"] == "READY"
    assert report["valid_anchor_count"] > 0
    assert report["return_distribution"]["status"] == "READY"
    assert report["barrier_time_statistics"]["UP_10"]["observed_events"] > 0
    assert report["barrier_time_statistics"]["DOWN_10"]["observed_events"] > 0
    assert report["barrier_time_statistics"]["UP_10"]["median_minutes"] is not None
    assert report["barrier_time_statistics"]["DOWN_10"]["mode_minutes"] is not None
    assert report["horizons"]["40"]["upper_10_reached_count"] > 0
    assert report["horizons"]["40"]["lower_10_reached_count"] > 0
    assert report["horizons"]["40"]["upper_independent_clusters"] > 0
    assert report["horizons"]["40"]["lower_independent_clusters"] > 0
    empirical = report["horizons"]["20"]["empirical_excursion"]
    assert empirical["max_return_q50"] >= 0.0
    assert empirical["min_return_q50"] <= 0.0
    assert report["external_context_feature_status"]["xrp_market_cap"] == "NOT_COLLECTED"


def test_discovery_report_cache_reuses_recent_matching_dataset(tmp_path: Path) -> None:
    store = PartitionedPriceStore(tmp_path / "prices")
    store.append(_prices())
    output = tmp_path / "reports" / "first_passage.json"

    first = generate_discovery_report(store, output, _config())
    second = generate_discovery_report(store, output, _config())

    assert first["generated_at_ms"] == second["generated_at_ms"]
    assert output.exists()
