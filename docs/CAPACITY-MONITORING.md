# ECS Capacity Monitoring

This document describes the capacity monitoring tools available for right-sizing ECS services.

## Overview

The deployer manages ECS service sizing (CPU, memory, replicas) via OpenTofu tfvars. The tools in this document help you:

- Monitor actual utilization vs. allocated resources
- Identify over-provisioned services (wasting money)
- Identify under-provisioned services (performance risk)
- Generate actionable recommendations for sizing changes

## Tools

### 1. AWS Compute Optimizer

AWS Compute Optimizer is a free AWS service that uses machine learning to analyze utilization patterns and provide right-sizing recommendations.

#### Enabling Compute Optimizer

**Option A: Via AWS CLI (one-time setup)**

```bash
aws compute-optimizer update-enrollment-status --status Active
```

**Option B: Via OpenTofu**

Add to your environment's `main.tf`:

```hcl
module "compute_optimizer" {
  source = "../../modules/compute-optimizer"
}
```

Then apply:

```bash
tofu -chdir=environments/staging apply
```

#### Viewing Recommendations

After enabling, wait 14 days for sufficient data collection, then:

**AWS Console:**
Navigate to Compute Optimizer → ECS services

**AWS CLI:**

```bash
# List all ECS recommendations
aws compute-optimizer get-ecs-service-recommendations

# Filter by cluster
aws compute-optimizer get-ecs-service-recommendations \
  --filters name=Finding,values=OVER_PROVISIONED
```

### 2. capacity-report.py

A Python script for on-demand capacity analysis that integrates with the deployer workflow.

#### Usage

```bash
# Report for staging environment (last 7 days)
uv run bin/capacity-report.py staging

# Report for production (last 14 days)
uv run bin/capacity-report.py production --days 14

# Compare against tfvars and generate suggested updates (RECOMMENDED)
uv run bin/capacity-report.py staging --tfvars environments/staging/terraform.tfvars

# JSON output for automation
uv run bin/capacity-report.py staging --format json

# Dry run (show what would be queried)
uv run bin/capacity-report.py staging --dry-run
```

#### Sample Output (without tfvars)

```
ECS Capacity Report - myapp-staging
Period: 2026-01-14 to 2026-01-21 (7 days)

Service        CPU Alloc  CPU Avg   CPU p95   Memory Alloc   Mem Avg   Mem p95   Status
──────────────────────────────────────────────────────────────────────────────────────────────
web            512            12%       45%   1024 MB            35%       52%   ⚠️ OVER-PROVISIONED
celery         256            78%       95%   512 MB             65%       82%   ⚠️ UNDER-PROVISIONED
api     512            25%       60%   1024 MB            40%       55%   ✓ OK
transcoder     1024            5%       85%   2048 MB            20%       45%   ✓ OK (bursty)

Recommendations:
• web: Consider reducing to cpu=256, memory=512
• celery: Consider increasing to cpu=512, memory=1024

Estimated monthly savings from right-sizing: ~$15
```

#### Sample Output (with tfvars comparison)

When you provide a `--tfvars` file, the script compares your current tfvars configuration
against the running infrastructure and recommendations, then generates ready-to-use tfvars:

```
ECS Capacity Report - myapp-staging
Period: 2026-01-14 to 2026-01-21 (7 days)

Service        CPU Alloc  CPU Avg   CPU p95   Memory Alloc   Mem Avg   Mem p95   Status
──────────────────────────────────────────────────────────────────────────────────────────────
web            512            12%       45%   1024 MB            35%       52%   ⚠️ OVER-PROVISIONED
celery         256            78%       95%   512 MB             65%       82%   ⚠️ UNDER-PROVISIONED

Recommendations:
• web: Consider reducing to cpu=256, memory=512
• celery: Consider increasing to cpu=512, memory=1024

Estimated monthly savings from right-sizing: ~$15

tfvars Comparison:
web:
  cpu: tfvars=512 → recommended=256
  memory: tfvars=1024 → recommended=512
celery:
  cpu: tfvars=256 → recommended=512
  memory: tfvars=512 → recommended=1024

Suggested tfvars:
services = {
  celery = {
    cpu           = 512
    memory        = 1024
    replicas      = 1
    load_balanced = false
  }
  web = {
    cpu           = 256
    memory        = 512
    replicas      = 2
    load_balanced = true
    port          = 8000
    health_check_path = "/health/"
  }
}
```

You can copy the suggested tfvars block directly into your `terraform.tfvars` file.

#### Classification Logic

| Status            | Criteria                                                  |
| ----------------- | --------------------------------------------------------- |
| OVER_PROVISIONED  | avg < 30% AND p95 < 50% for both CPU and memory           |
| UNDER_PROVISIONED | avg > 70% OR p95 > 90% for either CPU or memory           |
| BURSTY            | avg < 30% BUT p95 > 70% (workload is spiky, don't reduce) |
| OK                | Everything else                                           |

## Prerequisites

Both tools require Container Insights to be enabled on the ECS cluster. This is already configured in the `ecs-cluster` module:

```hcl
# modules/ecs-cluster/main.tf
resource "aws_ecs_cluster" "main" {
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
```

Container Insights costs approximately $0.30/task/month.

## Workflow

### The Feedback Loop

The capacity report creates a tight feedback loop between CloudWatch metrics and your tfvars:

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  CloudWatch     │────▶│ capacity-report  │────▶│  terraform.     │
│  Metrics        │     │    .py           │     │  tfvars         │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                         │
                                                         ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  ECS Services   │◀────│  tofu apply      │◀────│  Review &       │
│  (right-sized)  │     │                  │     │  Commit         │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### Regular Right-Sizing Review

1. Run capacity report with tfvars comparison:

   ```bash
   uv run bin/capacity-report.py \
     --environment production \
     --days 30 \
     --tfvars environments/production/terraform.tfvars
   ```

1. Review the output:

   - Check utilization percentages (avg and p95)
   - Review the tfvars comparison section
   - Copy the suggested tfvars if recommendations look reasonable

1. Update tfvars with the suggested values:

   ```bash
   # Edit environments/production/terraform.tfvars
   # Copy the "Suggested tfvars:" output from the report
   ```

1. Apply infrastructure changes:

   ```bash
   tofu -chdir=environments/production apply
   ```

1. Deploy to pick up new task definitions:

   ```bash
   uv run python bin/deploy.py myapp-production
   ```

1. Wait a few days and re-run the capacity report to verify the changes had the expected effect.

### Validating tfvars Match Running Infrastructure

Sometimes your tfvars may drift from what's actually running (e.g., manual changes, failed deployments). The capacity report will show this:

```bash
uv run bin/capacity-report.py \
  --environment staging \
  --tfvars environments/staging/terraform.tfvars
```

If you see output like:

```
tfvars Comparison:
web:
  cpu: tfvars=256 → running=512
```

This indicates your tfvars says 256, but the running task definition is 512. You should either:

- Update tfvars to match running (if the running value is correct)
- Run `tofu apply` to sync running to tfvars (if tfvars is correct)

### Before/After Comparison

Run capacity report before and after major deployments to track impact:

```bash
# Before deployment
uv run bin/capacity-report.py -e production --format json > before.json

# After deployment (wait a few days)
uv run bin/capacity-report.py -e production --format json > after.json

# Compare with jq
jq -r '.services[] | "\(.name): \(.cpu.avg_percent)% avg CPU"' before.json after.json
```

## Cost Estimates

| Service              | Monthly Cost            |
| -------------------- | ----------------------- |
| Container Insights   | ~$0.30/task             |
| Compute Optimizer    | Free                    |
| CloudWatch Dashboard | $3/dashboard (optional) |
| CloudWatch Alarms    | $0.10/alarm (optional)  |

## Future Enhancements

- CloudWatch alarms for sustained high utilization
- Automated PR creation with sizing recommendations
- Integration with deploy.py to show report before/after deployments
- Slack/email notifications for capacity issues
