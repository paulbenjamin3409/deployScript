from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cloud.aws.cli import AwsCli
from cloud.core.console import error, info, success, warn
from cloud.core.models import DeploymentConfig


_CREATION_GUIDE = """
To create an S3 + CloudFront static hosting setup:

  1. Create an S3 bucket (private, no public access):
       aws s3api create-bucket --bucket <name> --region <region> \\
           --create-bucket-configuration LocationConstraint=<region>
       aws s3api put-public-access-block --bucket <name> \\
           --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

  2. Create a CloudFront Origin Access Control (OAC):
       aws cloudfront create-origin-access-control \\
           --origin-access-control-config Name=<name>,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3

  3. Create a CloudFront distribution pointing to the S3 bucket using the OAC.
     Set the default root object to "index.html".
     Add a custom error response: 404 -> /index.html (HTTP 200) for SPA routing.

  4. Add a bucket policy allowing CloudFront OAC to read objects:
       {
         "Version": "2012-10-17",
         "Statement": [{
           "Effect": "Allow",
           "Principal": {"Service": "cloudfront.amazonaws.com"},
           "Action": "s3:GetObject",
           "Resource": "arn:aws:s3:::<bucket>/*",
           "Condition": {
             "StringEquals": {
               "AWS:SourceArn": "arn:aws:cloudfront::<account-id>:distribution/<dist-id>"
             }
           }
         }]
       }

  5. Set s3_bucket and cloudfront_distribution_id in config/local.yaml.
"""


# Explicit MIME types for web asset extensions.
# aws s3 sync on Windows resolves MIME types from the registry, which often
# maps .js to binary/octet-stream, breaking ES module loading in browsers.
# Each entry is (glob_patterns, Content-Type).
_TYPED_PASSES: list[tuple[list[str], str]] = [
    (["*.js", "*.mjs"], "application/javascript"),
    (["*.css"],         "text/css"),
    (["*.html"],        "text/html; charset=utf-8"),
    (["*.json"],        "application/json"),
    (["*.svg"],         "image/svg+xml"),
]


@dataclass
class S3CloudFrontProvider:
    config: DeploymentConfig
    cli: AwsCli
    workspace_root: str

    def ensure_resources(self) -> None:
        self._require_s3_bucket()
        self._require_cloudfront_distribution()

    def deploy_app(self) -> None:
        self._build_frontend(self.workspace_root)
        dist_path = os.path.join(self.workspace_root, self.config.dist_dir)
        self._sync_to_s3(dist_path)
        self._set_asset_cache_headers()
        self._invalidate_cloudfront()

    def _build_frontend(self, workspace_root: str) -> None:
        info("Building React SPA...")
        npm_cmd = shutil.which("npm")
        if not npm_cmd:
            error("npm not found on PATH. Install Node.js to build the frontend.")
            raise RuntimeError("npm not found")
        result = subprocess.run([npm_cmd, "run", "build"], cwd=workspace_root)
        if result.returncode != 0:
            error("Frontend build failed.")
            raise RuntimeError("Frontend build failed")
        success("Frontend build completed.")

    def _require_s3_bucket(self) -> None:
        info(f"Checking S3 bucket: {self.config.s3_bucket}")
        if not self.config.s3_bucket:
            error("s3_bucket is not set in config. Set it in config/local.yaml or pass --s3-bucket.")
            raise RuntimeError("s3_bucket not configured")
        result = self.cli.cmd(
            ["s3api", "head-bucket", "--bucket", self.config.s3_bucket],
            check=False,
        )
        if result.returncode != 0:
            aws_error = (result.stderr or result.stdout or "").strip()
            error(
                f"S3 bucket '{self.config.s3_bucket}' does not exist or is not accessible."
                + (f"\n  AWS: {aws_error}" if aws_error else "")
                + f"\n{_CREATION_GUIDE}"
            )
            raise RuntimeError(f"S3 bucket '{self.config.s3_bucket}' not found")
        success(f"S3 bucket '{self.config.s3_bucket}' verified.")

    def _require_cloudfront_distribution(self) -> None:
        info(f"Checking CloudFront distribution: {self.config.cloudfront_distribution_id}")
        if not self.config.cloudfront_distribution_id:
            error(
                "cloudfront_distribution_id is not set in config. "
                "Set it in config/local.yaml or pass --cloudfront-distribution-id."
            )
            raise RuntimeError("cloudfront_distribution_id not configured")
        result = self.cli.cmd(
            ["cloudfront", "get-distribution", "--id", self.config.cloudfront_distribution_id],
            check=False,
        )
        if result.returncode != 0:
            aws_error = (result.stderr or result.stdout or "").strip()
            error(
                f"CloudFront distribution '{self.config.cloudfront_distribution_id}' "
                f"does not exist or is not accessible."
                + (f"\n  AWS: {aws_error}" if aws_error else "")
                + f"\n{_CREATION_GUIDE}"
            )
            raise RuntimeError(
                f"CloudFront distribution '{self.config.cloudfront_distribution_id}' not found"
            )
        success(f"CloudFront distribution '{self.config.cloudfront_distribution_id}' verified.")

    def _sync_to_s3(self, dist_path: str) -> None:
        if not os.path.isdir(dist_path):
            error(f"Build output folder '{self.config.dist_dir}' not found at: {dist_path}")
            raise RuntimeError("Missing build output")

        s3_dest = f"s3://{self.config.s3_bucket}/"
        all_typed_exts = [ext for exts, _ in _TYPED_PASSES for ext in exts]

        # Pass 1: full sync with --delete for stale file removal and binary asset
        # upload (images, fonts, wasm, etc.). Typed extensions are excluded here
        # so they do not get the wrong Windows MIME type at this stage.
        info(f"Syncing binary assets and removing stale files from s3://{self.config.s3_bucket}/...")
        binary_args = (
            ["s3", "sync", dist_path, s3_dest, "--delete", "--region", self.config.aws_region]
            + [arg for ext in all_typed_exts for arg in ["--exclude", ext]]
        )
        result = self.cli.cmd(binary_args, capture_output=False, check=False)
        if result.returncode != 0:
            error("S3 sync (binary pass) failed.")
            raise RuntimeError("S3 sync failed")

        # Pass 2+: use aws s3 cp (local → S3) for each typed extension group.
        # Unlike sync, cp always uploads regardless of ETag, so the correct
        # Content-Type is guaranteed even when the file content has not changed
        # since the previous deploy (which would cause sync to skip the file).
        for exts, content_type in _TYPED_PASSES:
            label = " ".join(exts)
            info(f"Uploading {label} ({content_type})...")
            include_args = [arg for ext in exts for arg in ["--include", ext]]
            cp_args = (
                [
                    "s3", "cp", dist_path, s3_dest,
                    "--recursive",
                    "--exclude", "*",
                    "--content-type", content_type,
                    "--region", self.config.aws_region,
                ]
                + include_args
            )
            result = self.cli.cmd(cp_args, capture_output=False, check=False)
            if result.returncode != 0:
                error(f"S3 upload ({label}) failed.")
                raise RuntimeError("S3 upload failed")

        success("S3 sync completed.")

    def _set_asset_cache_headers(self) -> None:
        """Set long-lived cache headers on hashed asset files (Vite content-hash filenames).

        Runs a per-type pass so --content-type is always explicit alongside
        --cache-control when using --metadata-directive REPLACE. Without an
        explicit --content-type, a same-bucket cp on Windows re-detects the
        MIME type from the registry and can reset .js back to binary/octet-stream.
        """
        assets_s3_prefix = f"s3://{self.config.s3_bucket}/assets/"
        info("Setting cache headers on hashed assets (max-age=31536000,immutable)...")
        any_applied = False
        for exts, content_type in _TYPED_PASSES:
            include_args = [arg for ext in exts for arg in ["--include", ext]]
            result = self.cli.cmd(
                [
                    "s3", "cp",
                    assets_s3_prefix,
                    assets_s3_prefix,
                    "--recursive",
                    "--exclude", "*",
                    "--cache-control", "max-age=31536000,immutable",
                    "--content-type", content_type,
                    "--metadata-directive", "REPLACE",
                    "--region", self.config.aws_region,
                ] + include_args,
                check=False,
            )
            if result.returncode == 0:
                any_applied = True
        if any_applied:
            success("Asset cache headers applied.")
        else:
            warn("Could not set asset cache headers (assets/ may not exist yet); continuing.")

    def _invalidate_cloudfront(self) -> None:
        info(f"Creating CloudFront invalidation for distribution {self.config.cloudfront_distribution_id}...")
        result = self.cli.cmd(
            [
                "cloudfront", "create-invalidation",
                "--distribution-id", self.config.cloudfront_distribution_id,
                "--paths", "/*",
            ],
            capture_output=False,
            check=False,
        )
        if result.returncode != 0:
            error("CloudFront invalidation failed.")
            raise RuntimeError("CloudFront invalidation failed")
        success("CloudFront cache invalidated.")

    def get_cloudfront_url(self) -> str | None:
        try:
            data = self.cli.json(
                ["cloudfront", "get-distribution", "--id", self.config.cloudfront_distribution_id]
            )
            domain = data.get("Distribution", {}).get("DomainName")
            return f"https://{domain}" if domain else None
        except Exception:
            return None

    def validate_http(self, base_url: str) -> None:
        dist_path = os.path.join(self.workspace_root, self.config.dist_dir)
        info("Validating SPA deployment (homepage and asset checks)...")
        homepage_status = _http_status(base_url, timeout=30)
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
            asset_status = _http_status(asset_url, timeout=30)
            info(f"   Asset ({asset_path}) status: {asset_status}")
        else:
            info("   Asset: no asset path found in local build (skipping)")

        if homepage_status == "200" and (asset_status is None or asset_status == "200"):
            success("[VALIDATION] SPA is reachable on CloudFront.")
        else:
            warn(
                f"[VALIDATION] Warning: checks failed "
                f"(homepage={homepage_status}, asset={asset_status}). "
                "CloudFront may still be propagating (allow 1-2 minutes)."
            )


def _http_status(url: str, timeout: int) -> str:
    import shutil as _shutil
    curl = _shutil.which("curl")
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
