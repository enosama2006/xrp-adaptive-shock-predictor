from pathlib import Path

import pytest

from xasp.model_registry import ModelRegistry, PromotionEvidence


def evidence(version: str, *, pass_all: bool = True) -> PromotionEvidence:
    return PromotionEvidence(
        model_version=version,
        dataset_id="dataset-v1",
        commit_sha="abc123",
        walk_forward_passed=pass_all,
        calibration_passed=pass_all,
        leakage_audit_passed=pass_all,
        economic_gate_passed=pass_all,
        paper_gate_passed=pass_all,
        metrics={"brier": 0.18},
    )


def test_promotion_requires_every_gate(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")
    with pytest.raises(ValueError, match="every gate"):
        registry.promote(evidence("model-v1", pass_all=False))


def test_promote_then_rollback_restores_previous_champion(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")
    first = registry.promote(evidence("model-v1"))
    assert first.champion_version == "model-v1"

    second = registry.promote(evidence("model-v2"))
    assert second.champion_version == "model-v2"
    assert second.previous_champion_version == "model-v1"

    rolled_back = registry.rollback("live calibration degraded")
    assert rolled_back.champion_version == "model-v1"
    assert rolled_back.previous_champion_version is None


def test_quarantine_demotes_current_champion(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry.json")
    registry.promote(evidence("model-v1"))
    registry.promote(evidence("model-v2"))

    state = registry.quarantine("model-v2", "numerical instability")
    assert state.champion_version == "model-v1"
    assert "model-v2" in state.quarantined_versions

    with pytest.raises(ValueError, match="quarantined"):
        registry.promote(evidence("model-v2"))
