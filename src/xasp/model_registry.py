"""Champion-challenger registry with explicit evidence gates and rollback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    model_version: str
    dataset_id: str
    commit_sha: str
    walk_forward_passed: bool
    calibration_passed: bool
    leakage_audit_passed: bool
    economic_gate_passed: bool
    paper_gate_passed: bool
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def promotable(self) -> bool:
        return all(
            (
                self.walk_forward_passed,
                self.calibration_passed,
                self.leakage_audit_passed,
                self.economic_gate_passed,
                self.paper_gate_passed,
            )
        )


@dataclass(slots=True)
class RegistryState:
    schema_version: int = 1
    champion_version: str | None = None
    previous_champion_version: str | None = None
    quarantined_versions: list[str] = field(default_factory=list)
    promotion_history: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported registry schema")
        if self.champion_version in self.quarantined_versions:
            raise ValueError("champion cannot be quarantined")


class ModelRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RegistryState:
        if not self.path.exists():
            return RegistryState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        state = RegistryState(**payload)
        state.validate()
        return state

    def save(self, state: RegistryState) -> None:
        state.updated_at = datetime.now(UTC).isoformat()
        state.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(state), indent=2, sort_keys=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(self.path)

    def promote(self, evidence: PromotionEvidence) -> RegistryState:
        state = self.load()
        if evidence.model_version in state.quarantined_versions:
            raise ValueError("quarantined model cannot be promoted")
        if not evidence.promotable:
            raise ValueError("promotion evidence did not pass every gate")
        state.previous_champion_version = state.champion_version
        state.champion_version = evidence.model_version
        state.promotion_history.append(
            {
                "action": "PROMOTE",
                "model_version": evidence.model_version,
                "dataset_id": evidence.dataset_id,
                "commit_sha": evidence.commit_sha,
                "metrics": evidence.metrics,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        self.save(state)
        return state

    def quarantine(self, model_version: str, reason: str) -> RegistryState:
        if not model_version or not reason:
            raise ValueError("model_version and reason are required")
        state = self.load()
        if model_version not in state.quarantined_versions:
            state.quarantined_versions.append(model_version)
        if state.champion_version == model_version:
            state.champion_version = state.previous_champion_version
            state.previous_champion_version = None
        state.promotion_history.append(
            {
                "action": "QUARANTINE",
                "model_version": model_version,
                "reason": reason,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        self.save(state)
        return state

    def rollback(self, reason: str) -> RegistryState:
        state = self.load()
        if state.previous_champion_version is None:
            raise ValueError("no previous champion available for rollback")
        demoted = state.champion_version
        restored = state.previous_champion_version
        state.champion_version = restored
        state.previous_champion_version = None
        state.promotion_history.append(
            {
                "action": "ROLLBACK",
                "demoted_model_version": demoted,
                "restored_model_version": restored,
                "reason": reason,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        self.save(state)
        return state
