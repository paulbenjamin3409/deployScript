from __future__ import annotations

import json
import shutil
import subprocess

from cloud.core.console import error, info, warn
from cloud.core.exec import run_command


class AzureCli:
    def __init__(self) -> None:
        self.az_path: str | None = shutil.which("az") or shutil.which("az.cmd")

    def require_path(self) -> str:
        if not self.az_path:
            raise RuntimeError("Azure CLI path not initialized")
        return self.az_path

    def ensure_login(self) -> None:
        if not self.az_path:
            error("Azure CLI is not installed. Install from https://aka.ms/installazurecliwindows")
            raise RuntimeError("Azure CLI not installed")
        info("Checking Azure login status...")
        login_check = subprocess.run([self.az_path, "account", "show"], capture_output=True, text=True)
        if login_check.returncode != 0:
            warn("Not logged in to Azure. Initiating login...")
            login = subprocess.run([self.az_path, "login"])
            if login.returncode != 0:
                error("Azure login failed")
                raise RuntimeError("Azure login failed")
        info("Azure login verified")

    def cmd(self, args: list[str], *, capture_output: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
        az_path = self.require_path()
        return run_command([az_path, *args], capture_output=capture_output, check=check)

    def json(self, args: list[str]) -> dict:
        az_path = self.require_path()
        cmd = [az_path, *args]
        if "-o" not in args and "--output" not in args:
            cmd += ["-o", "json"]
        result = run_command(cmd)
        return json.loads(result.stdout or "{}")
