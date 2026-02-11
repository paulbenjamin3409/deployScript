from __future__ import annotations

import os
import shutil
from pathlib import Path

from cloud.core.models import WorkflowContext
from cloud.validation.base import ValidationResult


class AzCliValidator:
    name = "azure.cli.available"

    def validate(self, context: WorkflowContext) -> ValidationResult:
        az = shutil.which("az") or shutil.which("az.cmd")
        if not az:
            return ValidationResult(self.name, False, "Azure CLI not found on PATH.")
        return ValidationResult(self.name, True, f"Azure CLI found at {az}.")


class NodeBuildToolsValidator:
    name = "node.build.tools"

    def validate(self, context: WorkflowContext) -> ValidationResult:
        yarn_cmd = shutil.which("yarn.cmd") or shutil.which("yarn")
        yarn_ps1 = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/npm/yarn.ps1"
        npm_cmd = shutil.which("npm")
        if yarn_cmd or yarn_ps1.exists() or npm_cmd:
            return ValidationResult(self.name, True, "Node build tooling detected.")
        return ValidationResult(self.name, False, "Neither yarn nor npm found on PATH.")


class WebConfigValidator:
    name = "web.config.present"

    def validate(self, context: WorkflowContext) -> ValidationResult:
        runtime = (context.config.runtime or "").strip()
        # Linux runtimes use ':' (e.g., NODE:20-lts) and do not require web.config.
        if ":" in runtime:
            return ValidationResult(self.name, True, "web.config not required for Linux runtime.")

        web_config = Path(context.workspace_root) / "web.config"
        if web_config.exists():
            return ValidationResult(self.name, True, "web.config found.")
        return ValidationResult(self.name, False, "web.config not found in workspace root.")
