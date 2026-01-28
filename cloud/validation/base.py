from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cloud.core.models import WorkflowContext


@dataclass(frozen=True)
class ValidationResult:
    name: str
    ok: bool
    message: str


class Validator(Protocol):
    name: str

    def validate(self, context: WorkflowContext) -> ValidationResult:
        ...
