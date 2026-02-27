from __future__ import annotations

import json
import shutil
import subprocess

from cloud.core.console import error, info, warn
from cloud.core.exec import run_command


class AwsCli:
    def __init__(self, profile: str = "") -> None:
        self.aws_path: str | None = shutil.which("aws") or shutil.which("aws.cmd")
        self.profile = profile  # named profile, e.g. "AdministratorAccess-123456789012"

    def require_path(self) -> str:
        if not self.aws_path:
            raise RuntimeError("AWS CLI path not initialized")
        return self.aws_path

    def _profile_args(self) -> list[str]:
        """Returns ['--profile', '<name>'] if a profile is configured, else []."""
        return ["--profile", self.profile] if self.profile else []

    def ensure_login(self) -> None:
        if not self.aws_path:
            error("AWS CLI is not installed. Install from https://aws.amazon.com/cli/")
            raise RuntimeError("AWS CLI not installed")

        profile_label = f" (profile: {self.profile})" if self.profile else " (default profile)"
        info(f"Checking AWS authentication{profile_label}...")

        result = subprocess.run(
            [self.aws_path, *self._profile_args(), "sts", "get-caller-identity"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            aws_error = (result.stderr or result.stdout or "").strip()
            if aws_error:
                error(f"AWS authentication failed: {aws_error}")
            else:
                error("AWS authentication failed (no details returned by CLI).")

            sso_hint = (
                f"  aws sso login --profile {self.profile}"
                if self.profile
                else "  aws sso login --profile <your-profile-name>"
            )
            info(
                "\nTo reauthenticate, run one of the following:\n"
                f"{sso_hint}                    (IAM Identity Center / SSO)\n"
                "  aws configure                              (static access key credentials)\n"
                "\nThen re-run this script."
                + (
                    f"\n\nTip: set aws_profile: {self.profile} in config/local.yaml "
                    "to always use this profile."
                    if not self.profile
                    else ""
                )
            )
            if not self.profile:
                info(
                    "Available profiles on this machine (from 'aws configure list-profiles'):\n"
                    "  Run: aws configure list-profiles"
                )
            raise RuntimeError("AWS authentication required")

        identity = json.loads(result.stdout or "{}")
        info(f"Authenticated as: {identity.get('Arn', 'unknown')}")

    def cmd(
        self,
        args: list[str],
        *,
        capture_output: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        aws_path = self.require_path()
        return run_command([aws_path, *self._profile_args(), *args], capture_output=capture_output, check=check)

    def json(self, args: list[str]) -> dict:
        aws_path = self.require_path()
        result = run_command([aws_path, *self._profile_args(), *args])
        return json.loads(result.stdout or "{}")
