# Shared Environments for Simple Django Apps

## Overview

This document analyzes and designs a "shared environment" architecture for running multiple simple Django applications on shared infrastructure, reducing costs while maintaining operational simplicity.

**Target outcome**: Write this plan to `docs/PLAN.md` for future reference.

---

## Executive Summary

| Approach | Monthly Cost (10 apps) | Savings |
|----------|----------------------|---------|
| **Current**: 10 separate environments | ~$1,000-1,200 (staging) | - |
| **Proposed**: Shared infrastructure | ~$350-450 (staging) | **~$650-850/month** |

For both staging AND production shared environments: **~$1,300-1,700/month total savings**

---

## Eligibility Rules for Shared Environments

Apps qualify for shared environments if they meet ALL of these criteria:

| Rule | Rationale |
|------|-----------|
| **Single container/image** | Simplifies routing, no worker coordination |
| **Single ECS service** (web only) | No celery, no background workers |
| **Minimal resource needs** | ≤512 CPU, ≤1GB memory |
| **No special networking** | No VPC peering, no private link requirements |
| **Standard Django stack** | PostgreSQL + optional Redis |
| **Same team ownership** | Coordination for shared infra changes |

**Apps that should NOT be in shared environments:**
- Multi-container apps (web + celery + beat)
- High-traffic apps requiring dedicated ALB capacity
- Apps with strict isolation requirements
- Apps needing custom VPC configurations

---

## What Each Environment Currently Creates

Per standalone environment:

| Resource | Monthly Cost | Shareable? |
|----------|-------------|------------|
| VPC + NAT Gateway | ~$32-50 | Yes |
| ALB | ~$18-25 | Yes |
| ECS Cluster | $0 | Yes |
| RDS (db.t3.micro) | ~$15-35 | No (keep separate) |
| ElastiCache | ~$15-25 | Yes (with namespacing) |
| Cognito | $0 | Yes |
| IAM Roles | $0 | Partially |
| CloudWatch Logs | ~$2-10 | N/A |
| **Per-env overhead** | **~$80-145** | |

---

## Recommended Architecture: Option B

**Multiple environments sharing an infrastructure layer**

```
deployer-environments/
├── shared-infra-staging/        # Shared infrastructure (1 per tier)
│   ├── main.tf                  # VPC, NAT, ALB, ECS cluster, Cognito
│   ├── terraform.tfvars
│   └── terraform.tfstate
│
├── app1-staging/                # Per-app environment (lightweight)
│   ├── main.tf                  # RDS, target group, listener rule
│   ├── config.toml              # Standard config, references shared infra
│   └── terraform.tfvars         # App-specific sizing
│
├── app2-staging/
│   └── ...
│
└── shared-infra-production/     # Separate shared infra for production
    └── ...
```

### Why This Approach

1. **Preserves existing mental model**: Each app still has its own `config.toml` and environment directory
2. **Minimal deploy.py changes**: Works with existing deployment workflow
3. **Independent app lifecycles**: Deploy/modify one app without affecting others
4. **Gradual migration**: Move apps one at a time
5. **Clear blast radius**: Shared infra issues affect tier, not all apps globally

---

## Cost Breakdown

### Current: 10 Separate Staging Environments

| Component | Per Env | x10 |
|-----------|---------|-----|
| NAT Gateway | $32 | $320 |
| ALB | $20 | $200 |
| RDS (db.t3.micro) | $25 | $250 |
| ElastiCache | $20 | $200 |
| Misc (logs, etc.) | $5 | $50 |
| **Subtotal** | $102 | **$1,020** |

### Proposed: Shared Staging Environment

| Component | Shared | Per-App (x10) | Total |
|-----------|--------|---------------|-------|
| NAT Gateway | $32 | - | $32 |
| ALB | $30 | - | $30 |
| ECS Cluster | $0 | - | $0 |
| Cognito | $0 | - | $0 |
| RDS | - | $25 x 10 | $250 |
| Redis (shared) | $25 | - | $25 |
| Misc | $10 | - | $10 |
| **Total** | | | **$347** |

**Savings: ~$673/month for staging alone**

---

## Technical Design

### 1. New Module: `modules/shared-infrastructure`

Creates expensive shared resources only:

```hcl
# modules/shared-infrastructure/main.tf

module "vpc" {
  source = "../vpc"
  # ... standard VPC config
}

module "ecs_cluster" {
  source = "../ecs-cluster"
  # ... standard cluster config
}

module "alb" {
  source = "../alb"
  # Creates ALB but NO default target group
  # Per-app target groups created by app modules
}

# Optional: shared Cognito for staging auth
module "cognito" {
  source = "../cognito"
  count  = var.cognito_auth_enabled ? 1 : 0
}

# NO RDS - each app gets its own
# NO per-app ECR - each app manages its own
```

**Outputs:**
- `vpc_id`, `private_subnet_ids`, `public_subnet_ids`
- `ecs_cluster_name`, `ecs_cluster_arn`, `ecs_security_group_id`
- `alb_arn`, `alb_dns_name`, `alb_https_listener_arn`, `alb_security_group_id`
- `cognito_user_pool_id`, `cognito_user_pool_client_id` (if enabled)

### 2. New Module: `modules/app-in-shared-env`

Per-app resources referencing shared infrastructure:

```hcl
# modules/app-in-shared-env/main.tf

# Reference shared infrastructure via remote state
data "terraform_remote_state" "shared" {
  backend = "local"  # or "s3" for teams
  config = {
    path = var.shared_infra_state_path
  }
}

locals {
  shared = data.terraform_remote_state.shared.outputs
}

# Per-app RDS (separate database per app)
module "rds" {
  source             = "../rds"
  name_prefix        = "${var.app_name}-${var.environment}"
  vpc_id             = local.shared.vpc_id
  subnet_ids         = local.shared.private_subnet_ids
  ecs_security_group = local.shared.ecs_security_group_id
  instance_class     = var.db_instance_class
  # ...
}

# Per-app ALB Target Group
resource "aws_lb_target_group" "app" {
  name        = "${var.app_name}-${var.environment}"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = local.shared.vpc_id
  target_type = "ip"

  health_check {
    path = var.health_check_path
    # ...
  }
}

# Per-app ALB Listener Rule (host-based routing)
resource "aws_lb_listener_rule" "app" {
  listener_arn = local.shared.alb_https_listener_arn
  priority     = var.listener_rule_priority  # Unique per app: 100, 200, 300...

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    host_header {
      values = [var.domain_name]  # e.g., "app1.staging.example.com"
    }
  }
}

# Per-app ECR repository
module "ecr" {
  source       = "../ecr"
  name_prefix  = "${var.app_name}-${var.environment}"
  # ...
}

# Per-app IAM roles
resource "aws_iam_role" "ecs_task_execution" { ... }
resource "aws_iam_role" "ecs_task" { ... }
```

### 3. Config.toml Structure (Unchanged)

The beauty of this design: **config.toml structure doesn't change**

```toml
# app1-staging/config.toml

[environment]
type = "staging"
domain_name = "${tofu:domain_name}"

[infrastructure]
# These come from shared infra (via remote state outputs)
cluster_name = "${tofu:ecs_cluster_name}"
security_group_id = "${tofu:ecs_security_group_id}"
private_subnet_ids = "${tofu:private_subnet_ids}"
alb_dns_name = "${tofu:alb_dns_name}"

# These are app-specific (created by app-in-shared-env module)
execution_role_arn = "${tofu:ecs_execution_role_arn}"
task_role_arn = "${tofu:ecs_task_role_arn}"
target_group_arn = "${tofu:alb_target_group_arn}"
rds_instance_id = "${tofu:rds_instance_id}"
ecr_prefix = "${tofu:ecr_prefix}"

[database]
url = "${tofu:database_url}"

[services]
config = "${tofu:service_config}"
# ...
```

The per-app `main.tf` outputs both shared values (from remote state) and local values (from its own resources), so the config.toml placeholders resolve correctly.

### 4. Deploy.py Changes (Minimal)

Only one change needed:

```python
# Current (bin/deploy.py, approximately line 141):
self.cluster_name = f"{self.app_name}-{self.environment}-cluster"

# Change to:
self.cluster_name = infra.get("cluster_name")
if not self.cluster_name:
    # Fallback for standalone environments (backward compatibility)
    self.cluster_name = f"{self.app_name}-{self.environment}-cluster"
```

This allows:
- **Shared environments**: cluster_name from config.toml (e.g., `shared-staging-cluster`)
- **Standalone environments**: fallback to existing `{app}-{env}-cluster` convention

### 5. ALB Routing Strategy

**Host-based routing** (recommended):

```
app1.staging.example.com → app1 target group
app2.staging.example.com → app2 target group
...
```

Requirements:
- Wildcard certificate: `*.staging.example.com`
- DNS: CNAME or alias records for each app subdomain
- Each app's listener rule has unique priority (100, 200, 300, etc.)

---

## Implementation Plan

### Phase 1: Create New Modules

**Files to create:**

1. `modules/shared-infrastructure/`
   - `main.tf` - VPC, NAT, ALB, ECS cluster, Cognito
   - `variables.tf` - Inputs
   - `outputs.tf` - All values needed by per-app modules

2. `modules/app-in-shared-env/`
   - `main.tf` - Remote state reference, RDS, target group, listener rule, ECR, IAM
   - `variables.tf` - Inputs including shared state path, listener priority
   - `outputs.tf` - Matches current environment output structure

3. `example-deployer-environments/shared-infra-staging/`
   - Example shared infrastructure environment

4. `example-deployer-environments/app-on-shared-staging/`
   - Example per-app environment using shared infra

### Phase 2: Extend bin/init.py for Shared Environments

**File to modify:** `bin/init.py`

Add `--shared` flag to `environment` subcommand:

```bash
# Current (standalone):
python bin/init.py environment --app-name myapp --env-type staging

# New (shared):
python bin/init.py environment --app-name myapp --env-type staging --shared
```

**Workflow when `--shared` is specified:**

1. Check if `shared-infra-{env_type}` exists in environments directory
2. If not exists:
   - Prompt: "Shared infrastructure 'shared-infra-staging' doesn't exist. Create it? [y/N]"
   - If yes, create `shared-infra-staging/` with shared-infrastructure module
   - Show next steps for `tofu init && tofu apply`
3. Create per-app environment using `app-in-shared-env` module
4. Configure remote state reference to shared infrastructure

**Changes to init.py:**

```python
# Add to environment subcommand arguments:
env_parser.add_argument(
    "--shared",
    action="store_true",
    help="Use shared infrastructure (creates shared-infra-{env_type} if needed)",
)

# In cmd_environment():
if args.shared:
    shared_infra_name = f"shared-infra-{args.env_type}"
    shared_infra_path = get_environments_dir() / shared_infra_name

    if not shared_infra_path.exists():
        # Prompt to create shared infrastructure
        response = input(f"Shared infrastructure '{shared_infra_name}' doesn't exist. Create it? [y/N] ")
        if response.lower() == 'y':
            # Generate shared infrastructure files
            shared_files = generate_shared_infrastructure(env_type=args.env_type, ...)
            # Write files
            ...
            print(f"Created shared infrastructure: {shared_infra_path}")
            print("Run: ./bin/tofu.sh -chdir={shared_infra_path} init && apply")

    # Generate per-app environment using shared module
    files = generate_environment(
        app_name=args.app_name,
        env_type=args.env_type,
        shared=True,  # New parameter
        shared_infra_path=shared_infra_path,
        ...
    )
```

**Files to modify in `src/deployer/init/`:**

1. **`environment.py`** - Add shared environment support:

```python
def generate_environment(
    app_name: str,
    env_type: str,
    deploy_toml_path: Path = None,
    domain: str = None,
    shared: bool = False,           # NEW
    shared_infra_path: Path = None, # NEW
) -> dict[str, str]:
    """Generate environment files.

    If shared=True, generates lightweight environment using app-in-shared-env module.
    If shared=False (default), generates standalone environment using root module.
    """
    if shared:
        return generate_shared_app_environment(app_name, env_type, ...)
    else:
        # Existing logic
        ...

def generate_shared_infrastructure(env_type: str, domain_base: str = None) -> dict[str, str]:
    """Generate files for shared infrastructure environment.

    Args:
        env_type: 'staging' or 'production'
        domain_base: Base domain for wildcard cert (e.g., 'staging.example.com')

    Returns:
        Dict of {filepath: content} for main.tf, terraform.tfvars
    """
    env_name = f"shared-infra-{env_type}"
    env_dir = get_environments_dir() / env_name

    files = {}
    files[str(env_dir / "main.tf")] = generate_shared_infra_main_tf(env_type)
    files[str(env_dir / "terraform.tfvars")] = generate_shared_infra_tfvars(env_type, domain_base)
    return files

def generate_shared_app_environment(
    app_name: str,
    env_type: str,
    deploy_toml_path: Path = None,
    domain: str = None,
    shared_infra_path: Path = None,
    listener_priority: int = None,
) -> dict[str, str]:
    """Generate lightweight environment that uses shared infrastructure.

    Generates main.tf that:
    - References shared infra via terraform_remote_state
    - Creates per-app: RDS, target group, listener rule, ECR, IAM roles
    """
    env_name = f"{app_name}-{env_type}"
    env_dir = get_environments_dir() / env_name

    files = {}
    files[str(env_dir / "main.tf")] = generate_shared_app_main_tf(
        app_name, env_type, shared_infra_path, listener_priority
    )
    files[str(env_dir / "config.toml")] = generate_config_toml(app_name, env_type, domain)
    files[str(env_dir / "terraform.tfvars")] = generate_shared_app_tfvars(
        app_name, env_type, deploy_config, domain
    )
    return files
```

2. **`__init__.py`** - Export new functions:

```python
from deployer.init.environment import (
    generate_environment,
    generate_shared_infrastructure,  # NEW
)
```

**Listener priority management:**

Each app in a shared environment needs a unique ALB listener rule priority.

**Decision: Auto-assign** - Scan existing app environments and assign next available priority.

```python
def get_next_listener_priority(env_type: str) -> int:
    """Find next available listener priority for shared environment.

    Scans existing app environments using shared-infra-{env_type} and
    returns next available priority (100, 200, 300, ...).
    """
    env_dir = get_environments_dir()
    shared_infra_name = f"shared-infra-{env_type}"

    # Find all environments that reference this shared infra
    existing_priorities = []
    for env_path in env_dir.iterdir():
        if env_path.is_dir() and env_path.name != shared_infra_name:
            tfvars_path = env_path / "terraform.tfvars"
            if tfvars_path.exists():
                # Parse listener_priority from tfvars
                # ... extract priority if present
                pass

    # Return next available (100, 200, 300, ...)
    if not existing_priorities:
        return 100
    return max(existing_priorities) + 100
```

### Phase 3: Modify Deploy Script

**File to modify:**
- `bin/deploy.py` - Read cluster_name from config (with fallback)

### Phase 3: Documentation

**Files to create/update:**
- `docs/SHARED-ENVIRONMENTS.md` - New guide for shared environments
- `docs/DEPLOYMENT-GUIDE.md` - Add section on shared vs standalone
- `CLAUDE.md` - Add notes about shared infrastructure pattern

### User Workflow (After Implementation)

**Creating a new app in shared infrastructure:**

```bash
# Create app environment with --shared flag
# This will prompt to create shared-infra-staging if it doesn't exist
uv run python bin/init.py environment \
    --app-name myapp \
    --env-type staging \
    --shared \
    --domain myapp.staging.example.com

# Output:
# Shared infrastructure 'shared-infra-staging' doesn't exist. Create it? [y/N] y
# Created: deployer-environments/shared-infra-staging/main.tf
# Created: deployer-environments/shared-infra-staging/terraform.tfvars
#
# Next steps for shared infrastructure:
#   1. Edit shared-infra-staging/terraform.tfvars (set domain, Route53 zone)
#   2. Deploy: ./bin/tofu.sh -chdir=shared-infra-staging init && apply
#
# Created: deployer-environments/myapp-staging/main.tf
# Created: deployer-environments/myapp-staging/config.toml
# Created: deployer-environments/myapp-staging/terraform.tfvars
#
# Next steps for myapp:
#   1. Edit myapp-staging/terraform.tfvars (DB credentials, service sizing)
#   2. Deploy: ./bin/tofu.sh -chdir=myapp-staging init && apply
#   3. Deploy app: uv run python bin/deploy.py /path/to/deploy.toml myapp-staging
```

**Adding another app to existing shared infrastructure:**

```bash
# Shared infra already exists, so just creates per-app environment
uv run python bin/init.py environment \
    --app-name otherapp \
    --env-type staging \
    --shared \
    --domain otherapp.staging.example.com

# Output:
# Using existing shared infrastructure: shared-infra-staging
# Created: deployer-environments/otherapp-staging/main.tf
# ...
```

**Creating a standalone environment (current behavior, unchanged):**

```bash
# Without --shared, creates full standalone environment
uv run python bin/init.py environment \
    --app-name bigapp \
    --env-type staging \
    --domain bigapp-staging.example.com

# Creates standalone environment with its own VPC, NAT, ALB, etc.
```

### Phase 4: Migration (Per Shared Environment)

1. **Create shared infrastructure**
   ```bash
   cd deployer-environments
   mkdir shared-infra-staging
   # Configure main.tf, terraform.tfvars
   tofu init && tofu apply
   ```

2. **Migrate first app (pilot)**
   ```bash
   mkdir app1-staging
   # Configure to reference shared-infra-staging
   tofu init && tofu apply
   # Test deployment
   uv run python bin/deploy.py ../app1/deploy.toml app1-staging
   ```

3. **Verify and migrate remaining apps**
   - One at a time
   - Keep old environments running until verified
   - Update DNS to point to shared ALB

4. **Decommission old infrastructure**
   - After all apps migrated
   - `tofu destroy` old standalone environments

---

## Verification Plan

After implementation:

1. **Infrastructure verification**
   - [ ] Shared infra creates: VPC, NAT, ALB, ECS cluster
   - [ ] Per-app creates: RDS, target group, listener rule
   - [ ] Listener rules route correctly based on host header

2. **Deployment verification**
   - [ ] `deploy.py` works with shared environment
   - [ ] ECS service registers with correct target group
   - [ ] Health checks pass
   - [ ] App accessible at its subdomain

3. **Operational verification**
   - [ ] Logs appear in correct CloudWatch log group
   - [ ] Secrets accessible from SSM
   - [ ] Can run migrations via `ecs-run.py`
   - [ ] Staging scheduler works (if applicable)

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Shared ALB becomes bottleneck | Monitor LCU usage; promote high-traffic apps to standalone |
| One app's bad deploy affects routing | Listener rules are independent; careful priority management |
| Terraform state conflicts | Use remote state locking (S3 + DynamoDB) |
| Migration downtime | Blue-green: keep old running until new verified |
| Complexity increase | Clear documentation; eligibility rules limit scope |

---

## Decision Summary

| Question | Decision |
|----------|----------|
| One environment vs infrastructure layer? | **Infrastructure layer** (Option B) |
| Database sharing? | **No** - each app gets own RDS |
| Redis sharing? | **Yes** - with key namespacing |
| Routing strategy? | **Host-based** (subdomains) |
| Eligibility? | **Single-container apps only** |
| Migration approach? | **Gradual** - one app at a time |

---

## Deliverables

When this plan is approved:

1. **Write this document** to `docs/SHARED-ENVIRONMENTS.md`

2. **Create `modules/shared-infrastructure/`**
   - `main.tf` - VPC, NAT, ALB (no default target group), ECS cluster, Cognito
   - `variables.tf` - Inputs (region, CIDR, domain, etc.)
   - `outputs.tf` - All values needed by per-app modules

3. **Create `modules/app-in-shared-env/`**
   - `main.tf` - Remote state reference, RDS, target group, listener rule, ECR, IAM
   - `variables.tf` - Inputs including shared state path, listener priority
   - `outputs.tf` - Matches current environment output structure

4. **Update `bin/init.py`**
   - Add `--shared` flag to `environment` subcommand
   - Check for/create shared infrastructure
   - Generate appropriate environment type

5. **Update `src/deployer/init/environment.py`**
   - Add `generate_shared_infrastructure()` function
   - Add `generate_shared_app_environment()` function
   - Modify `generate_environment()` to support `shared=True`

6. **Update `bin/deploy.py`**
   - Read `cluster_name` from config.toml instead of constructing it
   - Maintain backward compatibility fallback

7. **Create example configurations**
   - `example-deployer-environments/shared-infra-staging/` - Example shared infrastructure
   - `example-deployer-environments/app-on-shared-staging/` - Example per-app environment

8. **Update documentation**
   - `docs/DEPLOYMENT-GUIDE.md` - Add shared environments section
   - `CLAUDE.md` - Add notes about shared infrastructure pattern

## Files to Modify (Summary)

| File | Change |
|------|--------|
| `bin/init.py` | Add `--shared` flag, shared infra creation logic |
| `src/deployer/init/environment.py` | Add shared environment generators |
| `src/deployer/init/__init__.py` | Export new functions |
| `bin/deploy.py` | Read cluster_name from config |
| `modules/alb/main.tf` | May need to make default target group optional |

## Files to Create (Summary)

| File | Purpose |
|------|---------|
| `modules/shared-infrastructure/main.tf` | Shared VPC, NAT, ALB, ECS cluster |
| `modules/shared-infrastructure/variables.tf` | Inputs |
| `modules/shared-infrastructure/outputs.tf` | Outputs for per-app modules |
| `modules/app-in-shared-env/main.tf` | Per-app resources |
| `modules/app-in-shared-env/variables.tf` | Inputs |
| `modules/app-in-shared-env/outputs.tf` | Outputs matching current pattern |
| `docs/SHARED-ENVIRONMENTS.md` | This plan + usage guide |



# Future Improvements

 - [ ] any way to identify flagging services as part of deployment and/or reporting?
 - [ ] clarify the term `modules`, which is now used both by tofu and by deploy.py
 - [ ] maybe standardize module names between tofu and deploy?
 - [ ] should there be any auditing/checking between tofu's modules and the deploy modules?
 - [ ] update audit script so it considers the variables that deploy.py injects so that we don't have to list DB_HOST etc in the ignored vars

