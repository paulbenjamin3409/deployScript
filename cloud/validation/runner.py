from __future__ import annotations

from typing import Iterable

from cloud.core.console import error, info
from cloud.validation.base import ValidationResult, Validator


def run_validations(validators: Iterable[Validator], context) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for validator in validators:
        result = validator.validate(context)
        results.append(result)
        if result.ok:
            info(f"[VALIDATION] {result.name}: {result.message}")
        else:
            error(f"[VALIDATION] {result.name}: {result.message}")
    return results
