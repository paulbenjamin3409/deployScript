from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cloud.aws.cli import AwsCli
from cloud.core.console import error, info, success, warn
from cloud.core.models import DeploymentConfig


_CREATION_GUIDE = """
To create the required AWS Lambda and API Gateway resources:

  1. Create an IAM execution role for the Lambda (if you don't have one):
       aws iam create-role --role-name beerplanner-lambda-role \\
           --assume-role-policy-document '{
             "Version":"2012-10-17",
             "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},
             "Action":"sts:AssumeRole"}]}'
       aws iam attach-role-policy --role-name beerplanner-lambda-role \\
           --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
       aws iam attach-role-policy --role-name beerplanner-lambda-role \\
           --policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite

  2. Build the Lambda package locally first (run this script once with a placeholder zip):
       cd <workspace>/server && npm run build

  3. Create the Lambda function (Node.js 20.x):
       aws lambda create-function \\
           --function-name <name> \\
           --runtime nodejs20.x \\
           --role arn:aws:iam::<account-id>:role/beerplanner-lambda-role \\
           --handler dist/lambda.lambdaHandler \\
           --zip-file fileb://lambda.zip \\
           --timeout 30 \\
           --memory-size 256 \\
           --environment Variables='{
             "SECRETS_MANAGER_SECRET_NAME":"<secret-name>",
             "CORS_ORIGIN":"https://<cloudfront-domain>"
           }'

  4. Create an HTTP API (API Gateway v2) and integrate it with the Lambda:
       aws apigatewayv2 create-api \\
           --name beerplanner-api \\
           --protocol-type HTTP \\
           --target arn:aws:lambda:<region>:<account-id>:function:<name>

       aws lambda add-permission \\
           --function-name <name> \\
           --statement-id apigateway-invoke \\
           --action lambda:InvokeFunction \\
           --principal apigateway.amazonaws.com \\
           --source-arn "arn:aws:execute-api:<region>:<account-id>:<api-id>/*/*"

  5. Create the Secrets Manager secret for GEOAPIFY_API_KEY:
       aws secretsmanager create-secret \\
           --name <secret-name> \\
           --secret-string '{"GEOAPIFY_API_KEY":"<your-key>"}'

  6. Set lambda_function_name and secrets_manager_secret_name in config/local.yaml.
"""


@dataclass
class LambdaApiProvider:
    config: DeploymentConfig
    cli: AwsCli
    workspace_root: str

    # Relative path from workspace_root to the server (Lambda) sub-project
    SERVER_SUBDIR = "server"

    def ensure_resources(self) -> None:
        self._require_lambda_function()

    def deploy_app(self) -> None:
        server_dir = os.path.join(self.workspace_root, self.SERVER_SUBDIR)
        self._build_lambda(server_dir)
        zip_path = self._package_lambda(server_dir)
        try:
            self._update_lambda_code(zip_path)
            self._update_lambda_config()
            self._wait_for_update()
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)

    def _require_lambda_function(self) -> None:
        info(f"Checking Lambda function: {self.config.lambda_function_name}")
        if not self.config.lambda_function_name:
            error(
                "lambda_function_name is not set in config. "
                "Set it in config/local.yaml or pass --lambda-function-name.\n"
                f"{_CREATION_GUIDE}"
            )
            raise RuntimeError("lambda_function_name not configured")
        result = self.cli.cmd(
            ["lambda", "get-function", "--function-name", self.config.lambda_function_name, "--region", self.config.aws_region],
            check=False,
        )
        if result.returncode != 0:
            aws_error = (result.stderr or result.stdout or "").strip()
            error(
                f"Lambda function '{self.config.lambda_function_name}' does not exist "
                f"in region '{self.config.aws_region}'."
                + (f"\n  AWS: {aws_error}" if aws_error else "")
                + f"\n{_CREATION_GUIDE}"
            )
            raise RuntimeError(f"Lambda function '{self.config.lambda_function_name}' not found")
        success(f"Lambda function '{self.config.lambda_function_name}' verified.")

    def _build_lambda(self, server_dir: str) -> None:
        if not os.path.isdir(server_dir):
            error(f"Lambda server directory not found: {server_dir}")
            raise RuntimeError(f"Server directory '{self.SERVER_SUBDIR}' not found in workspace")

        info("Building Lambda TypeScript...")
        npm_cmd = shutil.which("npm")
        if not npm_cmd:
            error("npm not found on PATH. Install Node.js to build the Lambda.")
            raise RuntimeError("npm not found")

        result = subprocess.run([npm_cmd, "run", "build"], cwd=server_dir)
        if result.returncode != 0:
            error("Lambda build failed.")
            raise RuntimeError("Lambda build failed")
        success("Lambda build completed.")

    def _package_lambda(self, server_dir: str) -> str:
        """
        Creates a production Lambda deployment ZIP containing:
          - dist/  (compiled JS from server/dist/)
          - node_modules/ (production-only, installed in a temp dir)

        Handler path: dist/lambda.lambdaHandler
        """
        dist_dir = os.path.join(server_dir, "dist")
        if not os.path.isdir(dist_dir):
            error(f"Lambda dist/ not found after build: {dist_dir}")
            raise RuntimeError("Lambda dist/ missing after build")

        package_json = os.path.join(server_dir, "package.json")
        lock_file = os.path.join(server_dir, "package-lock.json")

        info("Installing production dependencies for Lambda package...")
        with tempfile.TemporaryDirectory() as staging:
            # Copy package files into staging
            shutil.copy2(package_json, staging)
            if os.path.exists(lock_file):
                shutil.copy2(lock_file, staging)
            else:
                warn("package-lock.json not found; using npm install instead of npm ci.")

            npm_cmd = shutil.which("npm")
            if os.path.exists(os.path.join(staging, "package-lock.json")):
                install_cmd = [npm_cmd, "ci", "--omit=dev"]
            else:
                install_cmd = [npm_cmd, "install", "--omit=dev"]

            result = subprocess.run(install_cmd, cwd=staging)
            if result.returncode != 0:
                error("Failed to install production dependencies for Lambda.")
                raise RuntimeError("Lambda dependency install failed")

            # Build the ZIP: dist/ + node_modules/ at the root of the archive
            zip_name = f"lambda_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"
            zip_path = os.path.join(self.workspace_root, zip_name)
            info(f"Creating Lambda package: {zip_name}")

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # Add dist/
                for root, _, files in os.walk(dist_dir):
                    for f in files:
                        full = os.path.join(root, f)
                        arcname = os.path.relpath(full, server_dir).replace("\\", "/")
                        zf.write(full, arcname=arcname)

                # Add production node_modules/ from staging
                staging_nm = os.path.join(staging, "node_modules")
                if os.path.isdir(staging_nm):
                    for root, _, files in os.walk(staging_nm):
                        for f in files:
                            full = os.path.join(root, f)
                            arcname = os.path.relpath(full, staging).replace("\\", "/")
                            zf.write(full, arcname=arcname)
                else:
                    warn("No node_modules found after production install; ZIP will not include dependencies.")

        zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        info(f"Lambda package size: {zip_size_mb:.1f} MB")
        if zip_size_mb > 50:
            warn(
                f"Lambda ZIP is {zip_size_mb:.1f} MB. "
                "Consider moving large dependencies to a Lambda Layer for faster cold starts."
            )
        return zip_path

    def _update_lambda_code(self, zip_path: str) -> None:
        info(f"Uploading Lambda code to '{self.config.lambda_function_name}'...")
        result = self.cli.cmd(
            [
                "lambda", "update-function-code",
                "--function-name", self.config.lambda_function_name,
                "--zip-file", f"fileb://{zip_path}",
                "--region", self.config.aws_region,
            ],
            capture_output=False,
            check=False,
        )
        if result.returncode != 0:
            error("Lambda code upload failed.")
            raise RuntimeError("Lambda update-function-code failed")
        success("Lambda code uploaded.")

    def _update_lambda_config(self) -> None:
        """Update Lambda environment variables if configured."""
        env_vars: dict[str, str] = {}

        if self.config.secrets_manager_secret_name:
            env_vars["SECRETS_MANAGER_SECRET_NAME"] = self.config.secrets_manager_secret_name
        if self.config.cors_origin:
            env_vars["CORS_ORIGIN"] = self.config.cors_origin

        if not env_vars:
            info("No Lambda environment variables configured; skipping update.")
            return

        variables_str = ",".join(f"{k}={v}" for k, v in env_vars.items())
        info(f"Updating Lambda environment variables: {', '.join(env_vars.keys())}")
        result = self.cli.cmd(
            [
                "lambda", "update-function-configuration",
                "--function-name", self.config.lambda_function_name,
                "--environment", f"Variables={{{variables_str}}}",
                "--region", self.config.aws_region,
            ],
            capture_output=False,
            check=False,
        )
        if result.returncode != 0:
            error("Lambda configuration update failed.")
            raise RuntimeError("Lambda update-function-configuration failed")
        success("Lambda environment variables updated.")

    def _wait_for_update(self) -> None:
        """Wait for Lambda to finish updating before returning."""
        info("Waiting for Lambda update to complete...")
        result = self.cli.cmd(
            ["lambda", "wait", "function-updated", "--function-name", self.config.lambda_function_name, "--region", self.config.aws_region],
            check=False,
        )
        if result.returncode != 0:
            warn("Lambda wait timed out or failed; the function may still be updating.")
        else:
            success("Lambda update confirmed active.")

    def validate_health(self) -> None:
        """Invoke the Lambda /health route via a direct Lambda invocation."""
        import json

        info("Validating Lambda health via direct invocation...")
        # aws lambda invoke requires an output file; use a temp file (works on Windows and Unix)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            out_file = tmp.name

        try:
            result = self.cli.cmd(
                [
                    "lambda", "invoke",
                    "--function-name", self.config.lambda_function_name,
                    "--payload", json.dumps({
                        "version": "2.0",
                        "routeKey": "GET /health",
                        "rawPath": "/health",
                        "rawQueryString": "",
                        "headers": {"content-type": "application/json"},
                        "requestContext": {
                            "http": {"method": "GET", "path": "/health", "sourceIp": "127.0.0.1"}
                        },
                        "isBase64Encoded": False,
                    }),
                    "--region", self.config.aws_region,
                    out_file,
                ],
                check=False,
            )
        finally:
            if os.path.exists(out_file):
                os.remove(out_file)

        if result.returncode != 0:
            warn("Lambda health invocation failed; function may need a moment to initialize.")
        else:
            success("Lambda health check invocation succeeded.")
