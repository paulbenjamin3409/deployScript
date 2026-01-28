from __future__ import annotations

from cloud.core.models import WorkflowContext
from cloud.policy.base import PolicyResult


class LocationDefinedPolicy:
    name = "policy.location.defined"

    def evaluate(self, context: WorkflowContext) -> PolicyResult:
        if context.config.location:
            return PolicyResult(self.name, True, f"Location set to {context.config.location}.")
        return PolicyResult(self.name, False, "Location is empty.")
