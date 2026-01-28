from __future__ import annotations

from cloud.core.models import WorkflowContext
from cloud.workflows.registry import WorkflowRegistry


class WorkflowDecider:
    # decides based on the config.provider
    def decide(self, context: WorkflowContext, registry: WorkflowRegistry) -> str:
        if context.config.workflow:
            return context.config.workflow
        provider = context.config.provider.lower()
        if provider == "azure":
            return "azure.app_service.deploy"
        if provider == "aws":
            return "aws.website.deploy"
        return "azure.app_service.deploy"
