from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class DeploymentConfig:
    # Azure fields
    resource_group: str = ""
    web_app_name: str = ""
    location: str = "centralus"
    sku: str = "B1"
    runtime: str = "NODE:20-lts"
    dist_dir: str = "dist"
    quick_check: bool = False
    check_timeout_sec: int = 15

    # AWS fields
    aws_region: str = "us-east-1"
    aws_profile: str = ""
    s3_bucket: str = ""
    cloudfront_distribution_id: str = ""
    lambda_function_name: str = ""
    api_gateway_url: str = ""
    secrets_manager_secret_name: str = ""
    cors_origin: str = ""

    # Shared
    provider: str = "azure"
    workflow: Optional[str] = None
    iac_tool: Optional[str] = None
    validations: list[str] = field(default_factory=list)
    policy_checks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowContext:
    config: DeploymentConfig
    workspace_root: str
