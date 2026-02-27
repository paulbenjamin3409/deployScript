import argparse
import sys
from pathlib import Path

# Allow running this script directly without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cloud.core.console import error, info
from cloud.core.config import load_yaml_config
from cloud.core.models import DeploymentConfig, WorkflowContext
from cloud.workflows import AwsDeployWorkflow, AzureAppServiceDeployWorkflow, WorkflowDecider, WorkflowRegistry



def build_registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(AzureAppServiceDeployWorkflow())
    registry.register(AwsDeployWorkflow())
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and deploy to Azure App Service or AWS (S3/CloudFront + Lambda).")
    parser.add_argument("--workspace-root", default=None, help="Path to the app workspace (defaults to current directory).")
    parser.add_argument("--config", default=None, help="Path to local YAML config (defaults to config/local.yaml).")
    # Azure args
    parser.add_argument("--resource-group", default=None)
    parser.add_argument("--web-app-name", default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--sku", default=None)
    parser.add_argument("--runtime", default=None)
    parser.add_argument("--quick-check", action=argparse.BooleanOptionalAction, default=None, help="Short-circuit if site is already healthy.")
    parser.add_argument("--check-timeout-sec", type=int, default=None, help="Timeout (seconds) for HTTP checks.")
    # AWS args
    parser.add_argument("--aws-region", default=None, help="AWS region (default: us-east-1).")
    parser.add_argument("--aws-profile", default=None, help="AWS named profile (e.g. AdministratorAccess-990530464313).")
    parser.add_argument("--s3-bucket", default=None, help="S3 bucket name for the React SPA.")
    parser.add_argument("--cloudfront-distribution-id", default=None, help="CloudFront distribution ID.")
    parser.add_argument("--lambda-function-name", default=None, help="Lambda function name for the API.")
    parser.add_argument("--api-gateway-url", default=None, help="API Gateway invoke URL (e.g. https://xxxx.execute-api.us-east-1.amazonaws.com/).")
    parser.add_argument("--secrets-manager-secret-name", default=None, help="Secrets Manager secret name for GEOAPIFY_API_KEY.")
    parser.add_argument("--cors-origin", default=None, help="CORS origin for the Lambda (e.g. https://d1234.cloudfront.net).")
    # Shared args
    parser.add_argument("--provider", default=None, help="Cloud provider: azure or aws.")
    parser.add_argument("--workflow", default=None, help="Explicit workflow name to run.")
    parser.add_argument("--iac", default=None, help="IaC tool to orchestrate (terraform, bicep, cdk).")
    parser.add_argument("--validation", action="append", default=None, help="Validation name(s) to include.")
    parser.add_argument("--policy", action="append", default=None, help="Policy check name(s) to include.")
    args = parser.parse_args()

    default_config = DeploymentConfig()
    #Get Config from file
    config_path = Path(args.config).resolve() if args.config else Path("config") / "local.yaml"
    config_data = load_yaml_config(config_path)

    # Helper to pick config value from CLI, file, or default
    def pick(name: str, cli_value, fallback):
        if cli_value is not None:
            return cli_value
        if name in config_data:
            return config_data[name]
        return fallback


    config = DeploymentConfig(
        # Azure
        resource_group=pick("resource_group", args.resource_group, default_config.resource_group),
        web_app_name=pick("web_app_name", args.web_app_name, default_config.web_app_name),
        location=pick("location", args.location, default_config.location),
        sku=pick("sku", args.sku, default_config.sku),
        runtime=pick("runtime", args.runtime, default_config.runtime),
        dist_dir=pick("dist_dir", None, default_config.dist_dir),
        quick_check=pick("quick_check", args.quick_check, default_config.quick_check),
        check_timeout_sec=pick("check_timeout_sec", args.check_timeout_sec, default_config.check_timeout_sec),
        # AWS
        aws_region=pick("aws_region", args.aws_region, default_config.aws_region),
        aws_profile=pick("aws_profile", args.aws_profile, default_config.aws_profile),
        s3_bucket=pick("s3_bucket", args.s3_bucket, default_config.s3_bucket),
        cloudfront_distribution_id=pick("cloudfront_distribution_id", args.cloudfront_distribution_id, default_config.cloudfront_distribution_id),
        lambda_function_name=pick("lambda_function_name", args.lambda_function_name, default_config.lambda_function_name),
        api_gateway_url=pick("api_gateway_url", args.api_gateway_url, default_config.api_gateway_url),
        secrets_manager_secret_name=pick("secrets_manager_secret_name", args.secrets_manager_secret_name, default_config.secrets_manager_secret_name),
        cors_origin=pick("cors_origin", args.cors_origin, default_config.cors_origin),
        # Shared
        provider=pick("provider", args.provider, default_config.provider),
        workflow=pick("workflow", args.workflow, default_config.workflow),
        iac_tool=pick("iac_tool", args.iac, default_config.iac_tool),
        validations=list(pick("validations", args.validation, default_config.validations) or []),
        policy_checks=list(pick("policy_checks", args.policy, default_config.policy_checks) or []),
    )

    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else Path.cwd()
    #sets the context
    context = WorkflowContext(config=config, workspace_root=str(workspace_root))
    registry = build_registry()

    decider = WorkflowDecider()
    workflow_name = decider.decide(context, registry)
    workflow = registry.get(workflow_name)

    if not workflow:
        error(f"Workflow '{workflow_name}' not found. Available: {', '.join(registry.list_names())}")
        sys.exit(1)

    info(f"Starting workflow: {workflow_name}\n")
    result = workflow.run(context)
    if not result.ok:
        error(result.message)
        sys.exit(1)


if __name__ == "__main__":
    main()
