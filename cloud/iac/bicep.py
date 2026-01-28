from __future__ import annotations

from cloud.core.console import warn
from cloud.core.models import WorkflowContext
from cloud.iac.base import IaCOrchestrator


class BicepOrchestrator(IaCOrchestrator):
    name = "bicep"

    def plan(self, context: WorkflowContext) -> None:
        warn("Bicep planning not implemented yet.")

    def apply(self, context: WorkflowContext) -> None:
        warn("Bicep apply not implemented yet.")

    def destroy(self, context: WorkflowContext) -> None:
        warn("Bicep destroy not implemented yet.")
