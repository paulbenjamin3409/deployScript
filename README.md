# appServiceDeployScript
Platform engineering toolkit for deploying web apps to various providers

## Usage

Run the CLI entrypoint:

python scripts/deploy.py --resource-group <rg> --web-app-name <name> --location <region>

### Local config
Copy config/local.yaml.example to config/local.yaml and fill in values. The local file is ignored by git.
CLI flags override values in YAML.

### Optional flags
- --workflow: explicitly select a workflow (default: auto-decide)
- --provider: cloud provider (azure, aws)
- --iac: IaC tool to orchestrate (terraform, bicep, cdk)
- --validation: include specific validation(s)
- --policy: include specific policy check(s)

## Structure
- cloud/azure: Azure-specific providers and CLI helpers
- cloud/workflows: workflow registry, decisioning, and workflow implementations
- cloud/validation: pre-deploy validations
- cloud/policy: policy checks
- cloud/iac: IaC orchestrator interfaces (Terraform/Bicep/CDK)

## Dependencies
Install Python deps:
pip install -r requirements.txt

## Todo
- get aws setup
- get azure functions setup