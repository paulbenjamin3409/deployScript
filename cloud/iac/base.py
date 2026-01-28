from __future__ import annotations

from typing import Protocol

from cloud.core.models import WorkflowContext


class IaCOrchestrator(Protocol):
    name: str

    def plan(self, context: WorkflowContext) -> None:
        ...

    def apply(self, context: WorkflowContext) -> None:
        ...

    def destroy(self, context: WorkflowContext) -> None:
        ...
