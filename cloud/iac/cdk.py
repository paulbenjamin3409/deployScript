from __future__ import annotations

from cloud.core.console import warn
from cloud.core.models import WorkflowContext
from cloud.iac.base import IaCOrchestrator


class CdkOrchestrator(IaCOrchestrator):
    name = "cdk"

    def plan(self, context: WorkflowContext) -> None:
        warn("CDK planning not implemented yet.")

    def apply(self, context: WorkflowContext) -> None:
        warn("CDK apply not implemented yet.")

    def destroy(self, context: WorkflowContext) -> None:
        warn("CDK destroy not implemented yet.")
