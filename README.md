# Aethon

Phase 1 document ingestion and memory orchestration infrastructure on AWS.

## What is deployed

- Private S3 bucket for document uploads
- DynamoDB table for document metadata
- `upload_url` Lambda and HTTP API (`POST /uploads`) for presigned S3 PUT URLs

## Deployment model

Infrastructure is deployed with Terraform. GitHub Actions deploys automatically on pushes to `main` using **OIDC** — no long-lived AWS access keys are stored in GitHub.

One-time bootstrap creates:

- Terraform remote state bucket (S3)
- Terraform state lock table (DynamoDB)
- GitHub Actions OIDC provider and deployment IAM role

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/downloads) `>= 1.6.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) v2
- An AWS account with permissions to create IAM, S3, and DynamoDB resources
- GitHub repository: `amitvg1997/Aethon`

## 1. Configure local AWS credentials (one-time)

Bootstrap runs locally with your AWS credentials. Choose one option:

### Option A: AWS CLI profile (recommended)

```bash
aws configure --profile aethon
export AWS_PROFILE=aethon
export AWS_REGION=eu-central-1
```

### Option B: Environment variables

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION=eu-central-1
```

Verify:

```bash
aws sts get-caller-identity
```

## 2. Run bootstrap (one-time)

Bootstrap uses **local Terraform state** and creates the CI/CD foundation.

```bash
cd bootstrap
cp terraform.tfvars.example terraform.tfvars   # edit if needed
terraform init
terraform plan
terraform apply
```

Run bootstrap again whenever `bootstrap/` changes. The GitHub Actions workflow
only applies `Infrastructure/`, so deploy-role permission updates such as new
SNS, Secrets Manager, OpenSearch Serverless, or Bedrock actions must be applied
locally before the pipeline can create those resources.

Save the outputs:

```bash
terraform output github_actions_role_arn
terraform output terraform_state_bucket
terraform output terraform_lock_table
terraform output -raw backend_config
```

## 3. Configure GitHub repository settings

In GitHub: **Settings → Secrets and variables → Actions**

### Secret (required)

| Name | Value |
|------|-------|
| `AWS_ROLE_ARN` | `github_actions_role_arn` output from bootstrap |

### Variables (required)

| Name | Value |
|------|-------|
| `TF_STATE_BUCKET` | `terraform_state_bucket` output from bootstrap |
| `TF_LOCK_TABLE` | `terraform_lock_table` output from bootstrap |

### Variables (optional)

| Name | Default | Purpose |
|------|---------|---------|
| `AWS_REGION` | `eu-central-1` | AWS region for deploys |
| `TF_VAR_project_name` | `aethon` | Terraform `project_name` |
| `TF_VAR_environment` | `dev` | Terraform `environment` |

## 4. Configure local Terraform backend (optional, for local runs)

Create `Infrastructure/backend.hcl` from the bootstrap output:

```bash
cd Infrastructure
cp backend.hcl.example backend.hcl
# Replace placeholders with bootstrap output values
terraform init -backend-config=backend.hcl
terraform plan
```

`Infrastructure/backend.hcl` is gitignored.

## 5. Automatic deployment pipeline

Workflow file: [`.github/workflows/terraform.yml`](.github/workflows/terraform.yml)

| Event | Behavior |
|-------|----------|
| Pull request to `main` | `fmt`, `validate`, `plan`; plan posted as a PR comment |
| Push to `main` | `fmt`, `validate`, `plan`, `apply` |
| Manual | `workflow_dispatch` runs the same apply path as `main` |

Changes under `Infrastructure/`, `Lambda_src/`, or the workflow file trigger the pipeline.

## 6. Test the upload API

After deploy, read outputs from the latest workflow run or run locally:

```bash
cd Infrastructure
terraform output uploads_api_endpoint
terraform output documents_bucket_name
```

Request a presigned URL:

```bash
curl -s -X POST "$UPLOADS_API_ENDPOINT/uploads" \
  -H "Content-Type: application/json" \
  -d '{"filename":"example.pdf","content_type":"application/pdf"}'
```

Upload the file with the returned `upload_url`:

```bash
curl -X PUT "$UPLOAD_URL" \
  -H "Content-Type: application/pdf" \
  --upload-file example.pdf
```

## Security notes

- The upload API is currently **public**. Add Cognito or a JWT authorizer before production use.
- GitHub Actions uses short-lived credentials via OIDC; do not add `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` to GitHub for this pipeline.
- Bootstrap state stays local. Main infrastructure state is stored in the remote S3 backend.

## Project layout

```text
Aethon/
├── bootstrap/              # One-time OIDC + remote state setup (local state)
├── Infrastructure/         # Main Phase 1 Terraform stack (remote S3 state)
├── Lambda_src/upload_url/  # Presigned URL Lambda source
└── .github/workflows/      # GitHub Actions deployment pipeline
```
