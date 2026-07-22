import numpy as np
import pandas as pd

from xasp.drift import DriftThresholds, assess_drift, population_stability_index


def test_identical_distribution_has_near_zero_psi() -> None:
    values = pd.Series(np.linspace(0, 1, 500))
    assert population_stability_index(values, values) < 1e-9


def test_shifted_distribution_triggers_warning_or_critical() -> None:
    reference = pd.DataFrame({"feature": np.linspace(0, 1, 500)})
    current = pd.DataFrame({"feature": np.linspace(2, 3, 500)})
    finding = assess_drift(reference, current, ["feature"])
    assert finding.status == "CRITICAL"
    assert finding.reason == "quarantine_and_review"


def test_insufficient_rows_fails_closed_to_wait() -> None:
    reference = pd.DataFrame({"feature": [1.0, 2.0]})
    current = pd.DataFrame({"feature": [1.0, 2.0]})
    finding = assess_drift(reference, current, ["feature"])
    assert finding.status == "WAIT"


def test_brier_degradation_triggers_review() -> None:
    features = pd.DataFrame({"feature": np.linspace(0, 1, 200)})
    labels = ["NO_EVENT"] * 200
    reference_predictions = pd.DataFrame(
        {
            "actual_label": labels,
            "p_up_10": [0.02] * 200,
            "p_down_10": [0.03] * 200,
            "p_no_event": [0.95] * 200,
        }
    )
    current_predictions = pd.DataFrame(
        {
            "actual_label": labels,
            "p_up_10": [0.40] * 200,
            "p_down_10": [0.30] * 200,
            "p_no_event": [0.30] * 200,
        }
    )
    finding = assess_drift(
        features,
        features,
        ["feature"],
        reference_predictions=reference_predictions,
        current_predictions=current_predictions,
        thresholds=DriftThresholds(minimum_rows=100),
    )
    assert finding.status == "CRITICAL"
