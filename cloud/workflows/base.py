from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cloud.core.models import WorkflowContext


@dataclass(frozen=True)
class WorkflowResult:
    name: str
    ok: bool
    message: str


class Workflow(Protocol):
    name: str

    def run(self, context: WorkflowContext) -> WorkflowResult:
        ...
