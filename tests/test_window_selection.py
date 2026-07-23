from __future__ import annotations

from xasp.window_selection import select_candidate_windows


def test_window_selection_uses_passage_quantiles_and_independent_evidence() -> None:
    discovery = {
        "status": "READY",
        "barrier_time_statistics": {
            "UP_10": {
                "median_minutes": 500,
                "p75_minutes": 1_100,
                "p90_minutes": 3_000,
                "p95_minutes": 8_000,
            },
            "DOWN_10": {
                "median_minutes": 700,
                "p75_minutes": 1_500,
                "p90_minutes": 4_000,
                "p95_minutes": 9_000,
            },
        },
        "horizons": {
            "480": {
                "upper_10_reached_count": 20,
                "lower_10_reached_count": 25,
                "upper_independent_clusters": 5,
                "lower_independent_clusters": 6,
                "any_10pct_touch_rate": 0.004,
            },
            "720": {
                "upper_10_reached_count": 40,
                "lower_10_reached_count": 45,
                "upper_independent_clusters": 12,
                "lower_independent_clusters": 11,
                "any_10pct_touch_rate": 0.008,
            },
            "1440": {
                "upper_10_reached_count": 80,
                "lower_10_reached_count": 90,
                "upper_independent_clusters": 18,
                "lower_independent_clusters": 20,
                "any_10pct_touch_rate": 0.02,
            },
            "4320": {
                "upper_10_reached_count": 200,
                "lower_10_reached_count": 220,
                "upper_independent_clusters": 30,
                "lower_independent_clusters": 32,
                "any_10pct_touch_rate": 0.08,
            },
            "10080": {
                "upper_10_reached_count": 400,
                "lower_10_reached_count": 440,
                "upper_independent_clusters": 50,
                "lower_independent_clusters": 55,
                "any_10pct_touch_rate": 0.2,
            },
        },
    }

    selection = select_candidate_windows(discovery)

    assert selection["status"] == "READY"
    assert selection["quantile_aligned_horizons_minutes"] == [720, 1440, 4320, 10080]
    assert selection["evidence_supported_horizons_minutes"] == [720, 1440, 4320, 10080]
    assert selection["configured_supported_horizons_minutes"] == []
    assert selection["longer_supported_horizons_minutes"] == [720, 1440, 4320, 10080]
    assert selection["strategy_revision_required"] is True
    assert selection["automatic_model_reconfiguration"] is False


def test_window_selection_waits_for_discovery() -> None:
    selection = select_candidate_windows({"status": "WAIT"})

    assert selection["status"] == "WAIT"
    assert selection["automatic_model_reconfiguration"] is False
