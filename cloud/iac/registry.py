from __future__ import annotations

from cloud.iac.bicep import BicepOrchestrator
from cloud.iac.cdk import CdkOrchestrator
from cloud.iac.terraform import TerraformOrchestrator


def get_orchestrator(name: str | None):
    if not name:
        return None
    key = name.lower()
    mapping = {
        "terraform": TerraformOrchestrator(),
        "bicep": BicepOrchestrator(),
        "cdk": CdkOrchestrator(),
    }
    return mapping.get(key)
