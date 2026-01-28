from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cloud.core.models import WorkflowContext


@dataclass(frozen=True)
class PolicyResult:
    name: str
    ok: bool
    message: str


class PolicyCheck(Protocol):
    name: str

    def evaluate(self, context: WorkflowContext) -> PolicyResult:
        ...
