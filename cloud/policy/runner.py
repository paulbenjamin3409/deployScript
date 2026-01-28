from __future__ import annotations

from typing import Iterable

from cloud.core.console import error, info
from cloud.policy.base import PolicyCheck, PolicyResult


def run_policy_checks(checks: Iterable[PolicyCheck], context) -> list[PolicyResult]:
    results: list[PolicyResult] = []
    for check in checks:
        result = check.evaluate(context)
        results.append(result)
        if result.ok:
            info(f"[POLICY] {result.name}: {result.message}")
        else:
            error(f"[POLICY] {result.name}: {result.message}")
    return results
