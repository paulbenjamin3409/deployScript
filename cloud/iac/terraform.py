from __future__ import annotations

from cloud.core.console import warn
from cloud.core.models import WorkflowContext
from cloud.iac.base import IaCOrchestrator


class TerraformOrchestrator(IaCOrchestrator):
    name = "terraform"

    def plan(self, context: WorkflowContext) -> None:
        warn("Terraform planning not implemented yet.")

    def apply(self, context: WorkflowContext) -> None:
        warn("Terraform apply not implemented yet.")

    def destroy(self, context: WorkflowContext) -> None:
        warn("Terraform destroy not implemented yet.")
