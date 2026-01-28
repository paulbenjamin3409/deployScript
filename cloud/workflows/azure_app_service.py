from __future__ import annotations

import sys

from cloud.azure.app_service import AzureAppServiceProvider
from cloud.azure.cli import AzureCli
from cloud.core.console import error, info, success, warn
from cloud.core.models import WorkflowContext
from cloud.iac import get_orchestrator
from cloud.policy import LocationDefinedPolicy, run_policy_checks
from cloud.validation import AzCliValidator, NodeBuildToolsValidator, WebConfigValidator, run_validations
from cloud.workflows.base import WorkflowResult


class AzureAppServiceDeployWorkflow:
    name = "azure.app_service.deploy"
    
    def run(self, context: WorkflowContext) -> WorkflowResult:
        # validators to ensure we can deploy the app service; location
        validators = [AzCliValidator(), NodeBuildToolsValidator(), WebConfigValidator()]
        policies = [LocationDefinedPolicy()]

# ignores validations/policies not explicitly selected in the config
        if context.config.validations:
            selected = [v for v in validators if v.name in context.config.validations]
            unknown = sorted(set(context.config.validations) - {v.name for v in validators})
            if unknown:
                warn(f"Unknown validations ignored: {', '.join(unknown)}")
            validators = selected

        if context.config.policy_checks:
            selected = [p for p in policies if p.name in context.config.policy_checks]
            unknown = sorted(set(context.config.policy_checks) - {p.name for p in policies})
            if unknown:
                warn(f"Unknown policy checks ignored: {', '.join(unknown)}")
            policies = selected

        validation_results = run_validations(validators, context)
        if any(not result.ok for result in validation_results):
            error("Pre-deploy validation failed.")
            sys.exit(1)

        policy_results = run_policy_checks(policies, context)
        if any(not result.ok for result in policy_results):
            error("Policy checks failed.")
            sys.exit(1)

        cli = AzureCli()
        cli.ensure_login()
        provider = AzureAppServiceProvider(context.config, cli, context.workspace_root)

        #todo: future development to include IaC orchestration
        orchestrator = get_orchestrator(context.config.iac_tool)
        if context.config.iac_tool and not orchestrator:
            error(f"Unknown IaC tool '{context.config.iac_tool}'.")
            sys.exit(1)
        if orchestrator:
            info(f"Running IaC orchestration: {orchestrator.name}")
            orchestrator.plan(context)
            orchestrator.apply(context)
        
        if context.config.quick_check:
            passed, _ = provider.quick_check(context.config.check_timeout_sec, early=True)
            if passed:
                return WorkflowResult(self.name, True, "QuickCheck passed; skipping deployment.")

        provider.build_app()
        provider.copy_web_config()
        provider.ensure_resources()

        #maybe move this logic?
        should_deploy = True
        if context.config.quick_check:
            passed, _ = provider.quick_check(context.config.check_timeout_sec, early=False)
            if passed:
                should_deploy = False

        info("Deploying to Azure Web App...")
        info(f"   Resource Group: {context.config.resource_group}")
        info(f"   Web App Name: {context.config.web_app_name}")
        info(f"   Location: {context.config.location}\n")

        if not should_deploy:
            hostname = provider.get_hostname()
            warn("Skipping deployment: site already up (QuickCheck).")
            info(f"Your app is available at: https://{hostname}")
            return WorkflowResult(self.name, True, "QuickCheck skipped deployment.")

        provider.deploy_app()

        hostname = provider.get_hostname()
        success("Deployment completed successfully!\n")

        provider.restart()
        base_url = f"https://{hostname}"
        info(f"Your app is available at: {base_url}\n")

        provider.validate_http(base_url)
        provider.kudu_vfs_check()

        return WorkflowResult(self.name, True, "Deployment completed successfully.")
