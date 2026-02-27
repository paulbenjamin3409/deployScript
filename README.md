# deployScript

A platform engineering toolkit for deploying React SPAs to Azure App Service and AWS (S3 + CloudFront + Lambda + API Gateway). Handles building, packaging, resource provisioning, deployment, and post-deploy verification in a single command.

---

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Azure App Service](#azure-app-service)
  - [AWS (SPA + Lambda API)](#aws-spa--lambda-api)
  - [Config file vs CLI flags](#config-file-vs-cli-flags)
- [CLI Reference](#cli-reference)
- [Workflows](#workflows)
  - [azure.app_service.deploy](#azureapp_servicedeploy)
  - [aws.website.deploy](#awswebsitedeploy)
- [Validations](#validations)
- [Policy Checks](#policy-checks)
- [IaC Orchestration](#iac-orchestration)
- [Project Structure](#project-structure)
- [Extending the Toolkit](#extending-the-toolkit)
- [TODO](#todo)
---

## Overview

`deployScript` automates the full deployment lifecycle for front-end web applications:

| Feature | Azure | AWS |
|---|---|---|
| Build React SPA | yarn/npm run build | npm run build |
| Resource provisioning (idempotent) | Resource group, App Service plan, Web App | Verifies S3, CloudFront, Lambda exist |
| Deploy frontend | Zip deploy via `az webapp deploy` | S3 sync + CloudFront invalidation |
| Deploy API | — | Lambda zip upload + env var update |
| Post-deploy checks | HTTP + Kudu VFS | Lambda state, API Gateway health/CORS |
| QuickCheck (skip if healthy) | Yes | — |
| Selective validations | Yes | Yes |
| TODO: IaC orchestration hook | Terraform / Bicep / CDK (stubs) | — |

---

## Installation

**Prerequisites:** Python 3.11+, pip

```bash
pip install -r requirements.txt
```

**Cloud CLI prerequisites** (installed separately):

- Azure: [Azure CLI](https://aka.ms/installazurecliwindows) (`az`)
- AWS: [AWS CLI v2](https://aws.amazon.com/cli/) (`aws`)
- Node.js 18+ with `npm` (or `yarn`) for building your app

---

## Configuration

All deployment parameters can be supplied via a local YAML config file, CLI flags, or both. CLI flags always override the config file.

### Setup

```bash
cp config/local.yaml.example config/local.yaml
# Edit config/local.yaml — it is gitignored and never committed
```

### `config/local.yaml` reference

```yaml
# --- Azure App Service --------------------------------------------------------
resource_group: my-resource-group
web_app_name: my-webapp
location: eastus
sku: B1                    # App Service plan SKU (F1, B1, B2, S1, P1v3, ...)
runtime: NODE:20-lts       # Passed to az webapp create --runtime
dist_dir: dist             # Relative path to build output inside workspace
quick_check: false         # Skip deploy if site is already healthy
check_timeout_sec: 15      # HTTP timeout for QuickCheck

# --- AWS (S3 + CloudFront + Lambda API) ---------------------------------------
aws_region: us-east-1
aws_profile: AdministratorAccess-123456789012  # aws configure list-profiles
s3_bucket: my-spa-bucket
cloudfront_distribution_id: EXXXXXXXXXXXXX
lambda_function_name: my-api-function
api_gateway_url: https://<api-id>.execute-api.us-east-1.amazonaws.com/
secrets_manager_secret_name: myapp/api-key
cors_origin: https://<dist-id>.cloudfront.net

# --- Shared -------------------------------------------------------------------
provider: azure            # azure | aws  (used for auto workflow selection)
workflow: null             # Override workflow name explicitly (null = auto)
iac_tool: null             # terraform | bicep | cdk (null = skip)
validations: []            # Limit to named validators ([] = run all)
policy_checks: []          # Limit to named policy checks ([] = run all)
```

---

## Usage

### Azure App Service

Deploy a React SPA to an Azure Linux App Service:

```bash
# Minimal — uses values from config/local.yaml
python scripts/deploy.py --provider azure

# Fully explicit via CLI flags
python scripts/deploy.py \
  --provider azure \
  --workspace-root /path/to/my-react-app \
  --resource-group my-resource-group \
  --web-app-name my-webapp \
  --location eastus \
  --sku B1 \
  --runtime NODE:20-lts

# Skip deployment if the site is already up and returning HTTP 200
python scripts/deploy.py \
  --provider azure \
  --workspace-root /path/to/my-react-app \
  --resource-group my-resource-group \
  --web-app-name my-webapp \
  --location eastus \
  --quick-check
```

**What happens:**
1. Validates Azure CLI and Node build tools are installed
2. Checks the location policy
3. Ensures Azure login (prompts if not logged in)
4. Optionally runs QuickCheck — if the site is already Running + HTTP 200, skips deployment
5. Builds the React app (`yarn build` → `npm run build` fallback)
6. Copies `web.config` to the dist folder (skipped for Linux runtimes)
7. Creates the resource group, App Service plan, and Web App if they do not exist
8. Configures the app (sets `pm2 serve` as the startup command, disables Oryx build)
9. ZIPs the dist folder and deploys via `az webapp deploy --type zip --clean true`
10. Restarts the app
11. Validates the homepage and a static asset are reachable (HTTP 200)
12. Runs a Kudu VFS check to confirm `index.html` is present in `wwwroot`

### AWS (SPA + Lambda API)

Deploy a React SPA to S3/CloudFront and a Node.js Lambda API:

```bash
# Minimal — uses values from config/local.yaml
python scripts/deploy.py --provider aws

# Fully explicit via CLI flags
python scripts/deploy.py \
  --provider aws \
  --workspace-root /path/to/my-app \
  --s3-bucket my-spa-bucket \
  --cloudfront-distribution-id EXXXXXXXXXXXXX \
  --lambda-function-name my-api-function \
  --secrets-manager-secret-name myapp/api-key \
  --cors-origin https://<dist-id>.cloudfront.net \
  --api-gateway-url https://<api-id>.execute-api.us-east-1.amazonaws.com/

# With a named AWS SSO profile
python scripts/deploy.py \
  --provider aws \
  --aws-profile AdministratorAccess-123456789012 \
  --workspace-root /path/to/my-app \
  --s3-bucket my-spa-bucket \
  --cloudfront-distribution-id EXXXXXXXXXXXXX \
  --lambda-function-name my-api-function
```

**What happens:**
1. Validates AWS CLI and Node build tools are installed
2. Verifies AWS authentication (`aws sts get-caller-identity`)
3. Confirms the S3 bucket, CloudFront distribution, and Lambda function all exist — prints actionable creation instructions if any are missing
4. **Lambda API deploy:**
   - Builds TypeScript in the `./server/` sub-directory (`npm run build`)
   - Creates a production-only ZIP: `dist/` + `node_modules/` (installs with `npm ci --omit=dev` in a staging temp dir)
   - Uploads the ZIP (`aws lambda update-function-code`)
   - Updates Lambda environment variables (`SECRETS_MANAGER_SECRET_NAME`, `CORS_ORIGIN`)
   - Waits for the update to complete (`aws lambda wait function-updated`)
5. **SPA deploy:**
   - Builds the React frontend (`npm run build`)
   - Syncs binary assets to S3 with `--delete` to remove stale files
   - Re-uploads JS/CSS/HTML/JSON/SVG with explicit `Content-Type` headers (works around Windows registry MIME type misdetection)
   - Sets `Cache-Control: max-age=31536000,immutable` on hashed assets in `assets/`
   - Invalidates the CloudFront cache (`/*`)
6. **Post-deploy verification:**
   - HTTP check on the CloudFront URL (homepage + a static asset)
   - Lambda function state check (`Active` + `LastUpdateStatus=Successful`)
   - API Gateway `GET /health` → expects 200
   - API Gateway `OPTIONS /health` CORS preflight → verifies `Access-Control-Allow-Origin`
   - API Gateway unknown route → expects anything other than 502 (502 = crash-loop)

#### Expected workspace layout for AWS

```
my-app/
├── src/           # React SPA source
├── package.json   # Frontend package.json (npm run build → dist/)
├── dist/          # Built SPA (created by npm run build)
└── server/
    ├── src/       # Lambda TypeScript source
    ├── dist/      # Compiled Lambda JS (created by npm run build inside server/)
    └── package.json
```

#### AWS authentication

The script uses the AWS CLI profile you specify. To log in with IAM Identity Center (SSO):

```bash
aws sso login --profile AdministratorAccess-123456789012
```

If no profile is set, the default credential chain is used.

### Config file vs CLI flags

All parameters can come from either source. CLI flags always take priority:

```bash
# config/local.yaml sets provider: azure, but CLI overrides to aws
python scripts/deploy.py --provider aws --s3-bucket my-bucket
```

---

## CLI Reference

```
python scripts/deploy.py [options]

Shared
  --workspace-root PATH       Path to the app workspace (default: current directory)
  --config PATH               Path to YAML config (default: config/local.yaml)
  --provider PROVIDER         Cloud provider: azure | aws (default: azure)
  --workflow NAME             Explicit workflow name (default: auto-selected by provider)
  --iac TOOL                  IaC tool: terraform | bicep | cdk (default: none)
  --validation NAME           Run only this validator (repeatable, default: all)
  --policy NAME               Run only this policy check (repeatable, default: all)

Azure
  --resource-group NAME
  --web-app-name NAME
  --location REGION           e.g. eastus, westeurope, centralus
  --sku SKU                   App Service plan SKU (default: B1)
  --runtime RUNTIME           e.g. NODE:20-lts
  --quick-check / --no-quick-check
  --check-timeout-sec SECONDS

AWS
  --aws-region REGION         (default: us-east-1)
  --aws-profile PROFILE       Named AWS CLI profile
  --s3-bucket NAME
  --cloudfront-distribution-id ID
  --lambda-function-name NAME
  --api-gateway-url URL       HTTP API invoke URL (used for post-deploy checks)
  --secrets-manager-secret-name NAME
  --cors-origin URL           CORS origin set on the Lambda env var
```

---

## Workflows

Workflows are the top-level orchestration units. The correct workflow is selected automatically based on `--provider`, or you can override with `--workflow`.

### `azure.app_service.deploy`

Selected when `--provider azure` (the default).

Runs: validate → policy → login → IaC (optional) → QuickCheck → build → provision → deploy → verify

### `aws.website.deploy`

Selected when `--provider aws`.

Runs: validate → auth → resource check → Lambda deploy → SPA deploy → CloudFront invalidate → post-deploy checks

---

## Validations

Validators run before deployment and abort on failure. By default all applicable validators run. Use `--validation` to run a specific subset.

| Validator name | Provider | What it checks |
|---|---|---|
| `azure.cli.available` | Azure | `az` is on PATH |
| `node.build.tools` | Both | `yarn` or `npm` is on PATH |
| `web.config.present` | Azure | `web.config` exists in workspace root (skipped for Linux runtimes) |
| `aws.cli.available` | AWS | `aws` is on PATH |

**Examples:**

```bash
# Run only the Azure CLI check (skip the rest)
python scripts/deploy.py --provider azure --validation azure.cli.available

# Run two specific validators
python scripts/deploy.py \
  --provider azure \
  --validation azure.cli.available \
  --validation node.build.tools
```

Or in `config/local.yaml`:

```yaml
validations:
  - azure.cli.available
  - node.build.tools
```

---

## Policy Checks

Policy checks enforce governance rules before deployment. By default all applicable checks run. Use `--policy` to run a specific subset.

| Policy name | Provider | What it checks |
|---|---|---|
| `policy.location.defined` | Azure | `location` config field is non-empty |

**Example:**

```bash
python scripts/deploy.py \
  --provider azure \
  --policy policy.location.defined
```

Or in `config/local.yaml`:

```yaml
policy_checks:
  - policy.location.defined
```

---

## TODO: IaC Orchestration

The `--iac` flag hooks an IaC tool into the Azure workflow (runs plan + apply before the main deploy steps). Three tools are registered:

| Flag value | Orchestrator |
|---|---|
| `terraform` | `TerraformOrchestrator` |
| `bicep` | `BicepOrchestrator` |
| `cdk` | `CdkOrchestrator` |

> **Note:** All three orchestrators are currently stubs — they log a warning and return without running any commands. They define the extension point for future implementation.

```bash
python scripts/deploy.py --provider azure --iac terraform
```

---

## Project Structure

```
deployScript/
├── scripts/
│   └── deploy.py                  # CLI entrypoint
├── cloud/
│   ├── core/
│   │   ├── models.py              # DeploymentConfig + WorkflowContext dataclasses
│   │   ├── config.py              # YAML config loader
│   │   ├── console.py             # info / warn / error / success printers
│   │   ├── exec.py                # run_command() subprocess wrapper
│   │   └── base.py                # CloudProvider abstract base class
│   ├── workflows/
│   │   ├── base.py                # Workflow protocol + WorkflowResult
│   │   ├── registry.py            # WorkflowRegistry (name → workflow dict)
│   │   ├── decision.py            # WorkflowDecider (picks workflow by provider)
│   │   ├── azure_app_service.py   # azure.app_service.deploy workflow
│   │   └── aws_website.py         # aws.website.deploy workflow
│   ├── azure/
│   │   ├── cli.py                 # AzureCli wrapper (az commands)
│   │   └── app_service.py         # AzureAppServiceProvider (build/provision/deploy)
│   ├── aws/
│   │   ├── cli.py                 # AwsCli wrapper (aws commands, profile support)
│   │   ├── s3_cloudfront.py       # S3CloudFrontProvider (SPA build/sync/invalidate)
│   │   ├── lambda_api.py          # LambdaApiProvider (build/package/deploy Lambda)
│   │   └── verify.py              # AwsInfraVerifier (Lambda state + API Gateway checks)
│   ├── validation/
│   │   ├── base.py                # Validator protocol + ValidationResult
│   │   ├── validators.py          # Built-in validators
│   │   └── runner.py              # run_validations()
│   ├── policy/
│   │   ├── base.py                # PolicyCheck protocol + PolicyResult
│   │   ├── checks.py              # Built-in policy checks
│   │   └── runner.py              # run_policy_checks()
│   └── iac/
│       ├── base.py                # IaCOrchestrator protocol
│       ├── registry.py            # get_orchestrator() factory
│       ├── terraform.py           # TerraformOrchestrator (stub)
│       ├── bicep.py               # BicepOrchestrator (stub)
│       └── cdk.py                 # CdkOrchestrator (stub)
├── config/
│   ├── local.yaml.example         # Template — copy to local.yaml and fill in values
│   └── local.yaml                 # Your local config (gitignored)
└── requirements.txt               # pyyaml
```

---

## Extending the Toolkit

### Add a new workflow

1. Create `cloud/workflows/my_workflow.py` implementing the `Workflow` protocol:

```python
from cloud.workflows.base import WorkflowResult
from cloud.core.models import WorkflowContext

class MyWorkflow:
    name = "my.provider.deploy"

    def run(self, context: WorkflowContext) -> WorkflowResult:
        # your logic
        return WorkflowResult(self.name, True, "Done.")
```

2. Register it in `scripts/deploy.py`:

```python
registry.register(MyWorkflow())
```

3. Run it explicitly:

```bash
python scripts/deploy.py --workflow my.provider.deploy
```

### Add a new validator

```python
from cloud.validation.base import ValidationResult
from cloud.core.models import WorkflowContext

class DockerValidator:
    name = "docker.available"

    def validate(self, context: WorkflowContext) -> ValidationResult:
        import shutil
        docker = shutil.which("docker")
        if not docker:
            return ValidationResult(self.name, False, "Docker not found on PATH.")
        return ValidationResult(self.name, True, f"Docker found at {docker}.")
```

Add it to the `validators` list inside your workflow, then select it with `--validation docker.available`.

### Add a new policy check

```python
from cloud.policy.base import PolicyResult
from cloud.core.models import WorkflowContext

class SkuNotFreePolicy:
    name = "policy.sku.not_free"

    def evaluate(self, context: WorkflowContext) -> PolicyResult:
        if context.config.sku == "F1":
            return PolicyResult(self.name, False, "Free tier (F1) is not allowed in production.")
        return PolicyResult(self.name, True, f"SKU '{context.config.sku}' is acceptable.")
```

### Implement an IaC orchestrator

Subclass `IaCOrchestrator` and implement `plan`, `apply`, and `destroy`:

```python
from cloud.iac.base import IaCOrchestrator
from cloud.core.models import WorkflowContext

class TerraformOrchestrator(IaCOrchestrator):
    name = "terraform"

    def plan(self, context: WorkflowContext) -> None:
        # subprocess.run(["terraform", "plan", ...], cwd=context.workspace_root)
        ...

    def apply(self, context: WorkflowContext) -> None:
        # subprocess.run(["terraform", "apply", "-auto-approve", ...])
        ...

    def destroy(self, context: WorkflowContext) -> None:
        ...
```

Register in `cloud/iac/registry.py` → `get_orchestrator()`.

## TODO
1. IaC Orchestration
2. Possibly split it out to two different tools to reduce complexity 

