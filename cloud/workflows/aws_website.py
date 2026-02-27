from __future__ import annotations

import sys

from cloud.aws.cli import AwsCli
from cloud.aws.lambda_api import LambdaApiProvider
from cloud.aws.s3_cloudfront import S3CloudFrontProvider
from cloud.aws.verify import AwsInfraVerifier
from cloud.core.console import error, info, success, warn
from cloud.core.models import WorkflowContext
from cloud.validation import AwsCliValidator, NodeBuildToolsValidator, run_validations
from cloud.workflows.base import WorkflowResult


class AwsDeployWorkflow:
    name = "aws.website.deploy"

    def run(self, context: WorkflowContext) -> WorkflowResult:
        # Pre-deploy validators: AWS CLI and Node build tools
        validators = [AwsCliValidator(), NodeBuildToolsValidator()]

        if context.config.validations:
            selected = [v for v in validators if v.name in context.config.validations]
            unknown = sorted(set(context.config.validations) - {v.name for v in validators})
            if unknown:
                warn(f"Unknown validations ignored: {', '.join(unknown)}")
            validators = selected

        validation_results = run_validations(validators, context)
        if any(not result.ok for result in validation_results):
            error("Pre-deploy validation failed.")
            sys.exit(1)

        cli = AwsCli(profile=context.config.aws_profile)
        cli.ensure_login()

        spa_provider = S3CloudFrontProvider(context.config, cli, context.workspace_root)
        lambda_provider = LambdaApiProvider(context.config, cli, context.workspace_root)

        info("\nVerifying required AWS resources exist...")
        info(f"   Region:                     {context.config.aws_region}")
        info(f"   S3 Bucket:                  {context.config.s3_bucket or '(not set)'}")
        info(f"   CloudFront Distribution:    {context.config.cloudfront_distribution_id or '(not set)'}")
        info(f"   Lambda Function:            {context.config.lambda_function_name or '(not set)'}\n")

        # Check all resources up front so the user sees all errors at once
        resource_errors: list[str] = []
        try:
            spa_provider.ensure_resources()
        except RuntimeError as exc:
            resource_errors.append(str(exc))

        try:
            lambda_provider.ensure_resources()
        except RuntimeError as exc:
            resource_errors.append(str(exc))

        if resource_errors:
            error("One or more required AWS resources are missing. Fix the issues above and re-run.")
            sys.exit(1)

        # --- Deploy Lambda API ---
        info("\nDeploying Lambda API...")
        lambda_provider.deploy_app()

        # --- Deploy React SPA ---
        info("\nDeploying React SPA...")
        spa_provider.deploy_app()

        # --- Post-deploy validation ---
        cloudfront_url = spa_provider.get_cloudfront_url()
        if cloudfront_url:
            info(f"\nSPA available at: {cloudfront_url}")
            spa_provider.validate_http(cloudfront_url)
        else:
            warn("Could not determine CloudFront URL; skipping SPA HTTP validation.")

        # --- Infrastructure verification (Lambda state + API Gateway HTTP checks) ---
        verifier = AwsInfraVerifier(context.config, cli)
        check_results = verifier.run_all()
        failed_checks = [r for r in check_results if not r.ok]
        if failed_checks:
            warn(
                f"\n{len(failed_checks)} of {len(check_results)} infrastructure "
                "check(s) failed (see above). The deployment completed, but review "
                "any failures — they may indicate a misconfiguration."
            )
        else:
            success(f"\nAll {len(check_results)} infrastructure check(s) passed.")

        success("\nAWS deployment completed successfully!")
        return WorkflowResult(self.name, True, "AWS deployment completed successfully.")
