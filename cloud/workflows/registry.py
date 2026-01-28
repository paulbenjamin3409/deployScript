from __future__ import annotations

from cloud.workflows.base import Workflow


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        self._workflows[workflow.name] = workflow

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._workflows.keys())
