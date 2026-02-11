from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from cloud.azure.cli import AzureCli
from cloud.core.base import CloudProvider
from cloud.core.console import error, info, success, warn
from cloud.core.models import DeploymentConfig


@dataclass
class AzureAppServiceProvider(CloudProvider):
    config: DeploymentConfig
    cli: AzureCli
    workspace_root: str

    def ensure_resources(self) -> None:
        self.ensure_resource_group(self.config.resource_group, self.config.location)
        plan_name = f"{self.config.web_app_name}-plan"
        self.ensure_app_service_plan(plan_name, self.config.resource_group, self.config.location, self.config.sku)
        self.ensure_web_app(self.config.web_app_name, self.config.resource_group, plan_name)

    def deploy_app(self) -> None:
        self.configure_web_app(self.config.resource_group, self.config.web_app_name)
        dist_path = os.path.join(self.workspace_root, self.config.dist_dir)
        zip_path = self.create_zip(dist_path)
        try:
            self.deploy_package(self.config.resource_group, self.config.web_app_name, zip_path)
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)

    def ensure_resource_group(self, resource_group: str, location: str) -> None:
        info("Checking if resource group exists...")
        exists = self.cli.cmd(["group", "exists", "--name", resource_group]).stdout.strip().lower() == "true"
        if not exists:
            info(f"Creating resource group: {resource_group}")
            self.cli.cmd(["group", "create", "--name", resource_group, "--location", location], capture_output=False)
            success("Resource group created")
        else:
            success("Resource group already exists")

    def ensure_app_service_plan(self, plan_name: str, resource_group: str, location: str, sku: str) -> None:
        info("Checking if app service plan exists...")
        az_path = self.cli.require_path()
        plan_check = subprocess.run(
            [az_path, "appservice", "plan", "show", "--name", plan_name, "--resource-group", resource_group],
            capture_output=True,
            text=True,
        )
        if plan_check.returncode != 0:
            info(f"Creating app service plan: {plan_name}")
            created = subprocess.run(
                [
                    az_path,
                    "appservice",
                    "plan",
                    "create",
                    "--name",
                    plan_name,
                    "--resource-group",
                    resource_group,
                    "--location",
                    location,
                    "--sku",
                    sku,
                    "--is-linux",
                ]
            )
            if created.returncode != 0:
                error("Failed to create app service plan")
                raise RuntimeError("App service plan creation failed")
            success("App service plan created")
        else:
            success("App service plan already exists")

    def ensure_web_app(self, webapp_name: str, resource_group: str, plan_name: str) -> None:
        info("Checking if web app exists...")
        az_path = self.cli.require_path()
        webapp_check = subprocess.run(
            [az_path, "webapp", "show", "--name", webapp_name, "--resource-group", resource_group],
            capture_output=True,
            text=True,
        )
        if webapp_check.returncode != 0:
            info(f"Creating web app: {webapp_name}")
            created = subprocess.run(
                [
                    az_path,
                    "webapp",
                    "create",
                    "--name",
                    webapp_name,
                    "--resource-group",
                    resource_group,
                    "--plan",
                    plan_name,
                    "--runtime",
                    self.config.runtime,
                ]
            )
            if created.returncode != 0:
                error("Failed to create web app")
                raise RuntimeError("Web app creation failed")
            success("Web app created")
        else:
            success("Web app already exists")

    def configure_web_app(self, resource_group: str, webapp_name: str) -> None:
        info("Configuring app settings for static site...")
        self.cli.cmd(
            [
                "webapp",
                "config",
                "appsettings",
                "set",
                "--resource-group",
                resource_group,
                "--name",
                webapp_name,
                "--settings",
                "SCM_DO_BUILD_DURING_DEPLOYMENT=false",
                "ENABLE_ORYX_BUILD=false",
                "PORT=8080",
                "WEBSITES_PORT=8080",
            ],
            capture_output=False,
        )
        info("Setting startup command to serve /home/site/wwwroot with pm2 on port 8080...")
        self.cli.cmd(
            [
                "webapp",
                "config",
                "set",
                "--resource-group",
                resource_group,
                "--name",
                webapp_name,
                "--startup-file",
                "pm2 serve /home/site/wwwroot 8080 --no-daemon --spa",
            ],
            capture_output=False,
        )
        info("Setting health check path to /index.html...")
        self.cli.cmd(
            [
                "webapp",
                "update",
                "--resource-group",
                resource_group,
                "--name",
                webapp_name,
                "--set",
                "siteConfig.healthCheckPath=/index.html",
            ],
            capture_output=False,
        )

    def build_app(self) -> None:
        info("Building React application...")
        yarn_cmd = shutil.which("yarn.cmd") or shutil.which("yarn")
        yarn_ps1 = Path(os.environ.get("USERPROFILE", "")) / "AppData/Roaming/npm/yarn.ps1"
        npm_cmd = shutil.which("npm")

        if yarn_cmd:
            cmd = [yarn_cmd, "build"]
        elif yarn_ps1.exists():
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(yarn_ps1), "build"]
        elif npm_cmd:
            warn("yarn not found; falling back to npm run build")
            cmd = [npm_cmd, "run", "build"]
        else:
            error("Neither yarn nor npm found on PATH. Please install Node.js tooling.")
            raise RuntimeError("Node tooling not found")

        result = subprocess.run(cmd, cwd=self.workspace_root)
        if result.returncode != 0:
            error("Build failed")
            raise RuntimeError("Build failed")
        success("Build completed successfully")

    def copy_web_config(self) -> None:
        info("Copying web.config to dist folder...")
        runtime = (self.config.runtime or "").strip()
        source = os.path.join(self.workspace_root, "web.config")
        dest_dir = os.path.join(self.workspace_root, self.config.dist_dir)
        dest = os.path.join(dest_dir, "web.config")
        if not os.path.exists(source):
            if ":" in runtime:
                info("web.config not required for Linux runtime; skipping copy.")
                return
            error("web.config not found in project root")
            raise RuntimeError("web.config missing")
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(source, dest)

    def quick_check(self, timeout: int, early: bool = False) -> tuple[bool, Optional[str]]:
        label = "QuickCheck (early)" if early else "QuickCheck"
        info(f"{label}: verifying app status and HTTP reachability...")
        try:
            site_info = self.cli.json(
                ["webapp", "show", "--name", self.config.web_app_name, "--resource-group", self.config.resource_group]
            )
        except subprocess.CalledProcessError:
            warn("QuickCheck could not retrieve site info; continuing with deployment.")
            return False, None
        hostname = site_info.get("defaultHostName")
        state = site_info.get("state")
        if not hostname:
            warn("QuickCheck missing hostname; continuing.")
            return False, None
        base_url = f"https://{hostname}"
        status = self.http_status(base_url, timeout)
        info(f"   State: {state}, HTTP: {status}")
        if state == "Running" and status == "200":
            success("QuickCheck passed - site is up.")
            info(f"Your app is available at: {base_url}")
            return True, base_url
        return False, base_url

    def create_zip(self, dist_path: str) -> str:
        if not os.path.isdir(dist_path):
            error(f"Build output folder '{self.config.dist_dir}' not found.")
            raise RuntimeError("Missing build output")
        zip_name = f"deploy_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = os.path.join(self.workspace_root, zip_name)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        info("Creating deployment package...")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(dist_path):
                for f in files:
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, dist_path).replace("\\", "/")
                    if rel_path.strip():
                        zf.write(full_path, arcname=rel_path)
        return zip_path

    def deploy_package(self, resource_group: str, webapp_name: str, zip_path: str) -> None:
        info("Deploying package via Azure CLI (zip deploy)...")
        az_path = self.cli.require_path()
        result = subprocess.run(
            [
                az_path,
                "webapp",
                "deploy",
                "--resource-group",
                resource_group,
                "--name",
                webapp_name,
                "--src-path",
                zip_path,
                "--type",
                "zip",
                "--clean",
                "true",
            ]
        )
        if result.returncode != 0:
            error("Deployment failed")
            raise RuntimeError("Deployment failed")

    def get_hostname(self) -> str:
        result = self.cli.cmd(
            [
                "webapp",
                "show",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.web_app_name,
                "--query",
                "defaultHostName",
                "-o",
                "tsv",
            ]
        )
        return (result.stdout or "").strip()

    def restart(self) -> None:
        info("Restarting web app...")
        self.cli.cmd(
            ["webapp", "restart", "--resource-group", self.config.resource_group, "--name", self.config.web_app_name],
            capture_output=False,
        )

    def validate_http(self, base_url: str) -> None:
        dist_path = os.path.join(self.workspace_root, self.config.dist_dir)
        info("Validating deployment (homepage and asset checks)...")
        homepage_status = self.http_status(base_url, timeout=30)
        info(f"   Homepage status: {homepage_status}")
        asset_path = None
        local_index = os.path.join(dist_path, "index.html")
        if os.path.exists(local_index):
            html = Path(local_index).read_text(encoding="utf-8", errors="ignore")
            match = re.search(r'href="/assets/[^"]+"|src="/assets/[^"]+"', html)
            if match:
                raw = match.group(0)
                asset_path = re.sub(r'^(href|src)="', "", raw)[:-1]
                if not asset_path.startswith("/"):
                    asset_path = "/" + asset_path
        asset_status = None
        if asset_path:
            asset_url = base_url.rstrip("/") + asset_path
            asset_status = self.http_status(asset_url, timeout=30)
            info(f"   Asset ({asset_path}) status: {asset_status}")
        else:
            info("   Asset: no asset path found in local build (skipping)")
        if homepage_status == "200" and (asset_status is None or asset_status == "200"):
            success("[VALIDATION] Success: site and assets are reachable.")
        else:
            warn(f"[VALIDATION] Warning: checks failed (homepage={homepage_status}, asset={asset_status}).")

    def kudu_vfs_check(self) -> None:
        info("Checking index.html via Kudu VFS...")
        try:
            hostnames = self.cli.json(
                [
                    "webapp",
                    "show",
                    "--resource-group",
                    self.config.resource_group,
                    "--name",
                    self.config.web_app_name,
                    "--query",
                    "enabledHostNames",
                ]
            )
            scm_host = None
            for host in hostnames or []:
                if host and ".scm." in host:
                    scm_host = host
                    break
            if not scm_host:
                warn("   SCM host not found; skipping VFS check.")
                return
            creds = self.cli.json(
                [
                    "webapp",
                    "deployment",
                    "list-publishing-credentials",
                    "--resource-group",
                    self.config.resource_group,
                    "--name",
                    self.config.web_app_name,
                ]
            )
            user = creds.get("publishingUserName", "")
            pwd = creds.get("publishingPassword", "")
            token = base64.b64encode(f"{user}:{pwd}".encode("ascii")).decode("ascii")
            headers = {"Authorization": f"Basic {token}"}
            idx_url = f"https://{scm_host}/api/vfs/site/wwwroot/index.html"
            req = urllib.request.Request(idx_url, method="HEAD", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    info(f"   index.html status: {resp.getcode()} (exists)")
                    return
            except urllib.error.HTTPError:
                warn("   index.html not found via VFS (or inaccessible). Listing top-level entries...")
            dir_url = f"https://{scm_host}/api/vfs/site/wwwroot/"
            dir_req = urllib.request.Request(dir_url, headers=headers)
            try:
                with urllib.request.urlopen(dir_req, timeout=20) as resp:
                    content = resp.read().decode("utf-8", errors="ignore")
                    entries = json.loads(content)
                    for entry in entries[:5]:
                        info(f"   - {entry.get('name')}")
            except Exception:
                warn("   Could not list wwwroot contents from VFS.")
        except Exception:
            warn("   VFS check encountered an issue; continuing.")

    @staticmethod
    def http_status(url: str, timeout: int) -> str:
        curl = shutil.which("curl")
        if curl:
            try:
                result = subprocess.run(
                    [curl, "-sS", "-o", os.devnull, "-w", "%{http_code}", url],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return (result.stdout or "000").strip()
            except Exception:
                return "000"
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return str(resp.getcode())
        except Exception:
            return "000"
