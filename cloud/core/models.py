from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class DeploymentConfig:
    resource_group: str = "appsvc_linux_centralus_basic"
    web_app_name: str = "pjweb-000002"
    location: str = "centralus"
    sku: str = "B1"
    runtime: str = "NODE:20-lts"
    dist_dir: str = "dist"
    quick_check: bool = False
    check_timeout_sec: int = 15
    provider: str = "azure"
    workflow: Optional[str] = None
    iac_tool: Optional[str] = None
    validations: list[str] = field(default_factory=list)
    policy_checks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowContext:
    config: DeploymentConfig
    workspace_root: str
