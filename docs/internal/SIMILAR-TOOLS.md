# Similar Tools Comparison

A comparison of this deployer with similar tools in the AWS ECS and container deployment ecosystem, updated for 2025-2026.

## What This Deployer Does

This project provides an integrated workflow for deploying containerized applications to AWS ECS Fargate:

1. **Reusable OpenTofu/Terraform modules** for AWS infrastructure (VPC, ECS, RDS, ElastiCache, S3, ALB, ACM, Cognito, WAF, etc.)
1. **Environment configurations** with per-environment instantiation (staging, production)
1. **Deploy script** (`deploy.py`) that reads TOML configs, builds Docker images, pushes to ECR, runs migrations, and deploys to ECS
1. **Shared environments** allowing multiple simple apps to share VPC, NAT Gateway, ALB, and ECS cluster
1. **Migration optimization** with hash-based skip when migrations are unchanged
1. **Supporting scripts** for Cognito access control, secrets management, and capacity reporting

______________________________________________________________________

## Configuration Architecture

A key differentiator of this deployer is its **three-layer configuration separation**:

### Layer 1: deploy.toml (Application Repository)

Defines **what to run** - the application's structure. Checked into the app's repository.

```toml
[application]
name = "myapp"

[images.web]
dockerfile = "Dockerfile"

[services.web]
image = "web"
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"

[environment]
DATABASE_URL = "${database_url}"
REDIS_URL = "${redis_url}"

[migrations]
enabled = true
command = ["python", "manage.py", "migrate"]
```

### Layer 2: config.toml (Per-Environment)

The **infrastructure bridge** - connects deploy.toml placeholders to actual infrastructure. Lives in the environments directory.

```toml
[aws]
deploy_profile = "deployer-app"
infra_profile = "deployer-infra"

[infrastructure]
cluster_name = "${tofu:ecs_cluster_name}"
security_group_id = "${tofu:ecs_security_group_id}"
ecr_prefix = "${tofu:ecr_prefix}"

[database]
host = "${tofu:db_host}"
credentials = "secretsmanager"

[services]
config = "${tofu:service_config}"
scaling = "${tofu:scaling_config}"
```

### Layer 3: terraform.tfvars (Per-Environment)

Defines **sizing and scaling** - CPU, memory, replicas. Managed by ops/infrastructure team.

```hcl
services = {
  web = {
    cpu               = 256
    memory            = 512
    replicas          = 1
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
}

scaling = {
  web = {
    min_replicas = 1
    max_replicas = 4
    cpu_target   = 70
  }
}
```

### Why This Separation Matters

| Concern                                      | Owner     | Location                         | Changes When                           |
| -------------------------------------------- | --------- | -------------------------------- | -------------------------------------- |
| App structure (services, commands, env vars) | Developer | deploy.toml in app repo          | App architecture changes               |
| Infrastructure references                    | DevOps    | config.toml per-environment      | Infrastructure is provisioned/modified |
| Resource sizing (CPU, memory, replicas)      | Ops       | terraform.tfvars per-environment | Scaling needs change                   |

**Benefits:**

- No environment-specific values in application repositories
- Clear ownership boundaries between dev and ops
- Fail-fast on missing configuration (no silent fallbacks)
- Infrastructure changes don't require app repo commits

______________________________________________________________________

## Similar Tools Comparison

### AWS Copilot CLI

**URL:** https://aws.github.io/copilot-cli/ **GitHub:** https://github.com/aws/copilot-cli

The closest alternative. Copilot is AWS's official CLI for containerized applications on ECS/Fargate and App Runner.

**What it does:**

- Deploys containerized applications from a Dockerfile
- Creates all infrastructure automatically (VPC, ECS cluster, ALB)
- Supports multiple service patterns: Load Balanced Web Service, Backend Service, Worker Service, Scheduled Job
- Manages multiple environments across regions and accounts
- Sets up CI/CD pipelines with AWS CodePipeline
- Provides `copilot logs`, `copilot exec` for debugging

**Configuration:** Single YAML manifest per service

```yaml
name: api
type: Load Balanced Web Service
image:
  build: Dockerfile
  port: 8000
cpu: 256
memory: 512
count: 1
```

**Comparison:**

| Feature                | This Deployer    | AWS Copilot            |
| ---------------------- | ---------------- | ---------------------- |
| Config separation      | Yes (3-layer)    | No (single manifest)   |
| Infrastructure control | Full (Terraform) | Limited (abstractions) |
| Config format          | TOML             | YAML                   |
| Shared environments    | Yes              | No                     |
| Migrations             | Yes (ECS tasks)  | No built-in support    |
| Blue/green deployments | Via ECS native   | Yes                    |
| Learning curve         | Higher           | Lower                  |

**When to use Copilot:** Quick start, AWS-blessed approach, less infrastructure control needed.

______________________________________________________________________

### ecspresso

**URL:** https://github.com/kayac/ecspresso

A focused CLI tool specifically for ECS deployments. Now supports native ECS blue/green deployments.

**What it does:**

- Deploys task definitions and services to ECS
- Uses JSON/YAML/Jsonnet configuration files
- Template functions for environment variables, Terraform state, SSM parameters
- Supports rolling updates and blue/green deployments (ECS native or CodeDeploy)
- Can generate configs from existing ECS services via `ecspresso init`
- Plugin system for extensibility

**Configuration:** YAML/JSON task and service definitions

**Comparison:**

| Feature                  | This Deployer  | ecspresso                 |
| ------------------------ | -------------- | ------------------------- |
| Build Docker images      | Yes            | No                        |
| Provision infrastructure | Yes            | No                        |
| Deploy to ECS            | Yes            | Yes                       |
| Config separation        | Yes (3-layer)  | Partial (templates)       |
| Migrations               | Yes            | No                        |
| Terraform integration    | Yes            | Yes (tfstate plugin)      |
| Blue/green deployments   | Via ECS native | Yes (native + CodeDeploy) |

**When to use ecspresso:** Existing ECS infrastructure, only need deployment orchestration.

______________________________________________________________________

### ECS Compose-X

**URL:** https://docs.compose-x.io/ **GitHub:** https://github.com/compose-x/ecs_composex

Deploys applications to ECS using docker-compose files with extensions.

**What it does:**

- Converts docker-compose.yml to CloudFormation
- Creates AWS resources (ECS, VPC, RDS, ElastiCache, S3)
- Auto-configures IAM roles and networking
- "Lookup" feature to discover and use existing AWS resources

**Configuration:** Docker Compose YAML with x-\* extensions

**Comparison:**

| Feature           | This Deployer      | ECS Compose-X  |
| ----------------- | ------------------ | -------------- |
| Config format     | TOML               | docker-compose |
| IaC backend       | OpenTofu/Terraform | CloudFormation |
| Config separation | Yes (3-layer)      | No             |
| Migrations        | Yes                | No             |

**When to use ECS Compose-X:** Existing docker-compose files, CloudFormation preference.

______________________________________________________________________

### fabfuel/ecs-deploy

**URL:** https://github.com/fabfuel/ecs-deploy

Python CLI for ECS operational tasks.

**What it does:**

- Redeploys existing task definitions with new images
- Supports scaling and rollbacks
- Slack notifications, New Relic deployment tracking
- Cross-account deployment support

**Comparison:**

| Feature                  | This Deployer | ecs-deploy    |
| ------------------------ | ------------- | ------------- |
| Build images             | Yes           | No            |
| Provision infrastructure | Yes           | No            |
| Declarative config       | Yes           | No (CLI args) |
| Migrations               | Yes           | No            |

**When to use ecs-deploy:** Simple image updates to existing ECS services, operational scripting.

______________________________________________________________________

### terraform-aws-modules/ecs

**URL:** https://registry.terraform.io/modules/terraform-aws-modules/ecs/aws/latest

Community Terraform modules for ECS infrastructure.

**What it does:**

- Provides Terraform modules for ECS cluster and service
- Supports Fargate and EC2 capacity providers
- Well-maintained, widely used

**Comparison:**

This deployer's modules serve a similar purpose but are tailored for the three-layer configuration system. terraform-aws-modules provides just infrastructure; you still need deployment orchestration.

**When to use terraform-aws-modules:** Building your own deployment tooling, need just the Terraform layer.

______________________________________________________________________

### AWS CDK ECS Patterns

**URL:** https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_ecs_patterns-readme.html

High-level CDK constructs for common ECS patterns.

**What it does:**

- `ApplicationLoadBalancedFargateService` creates ECS + ALB in ~20 lines
- Auto-creates VPC, cluster, load balancer
- Now supports linear and canary deployment strategies (added Oct 2025)

**Configuration:** TypeScript/Python/Java CDK code

**Comparison:**

| Feature               | This Deployer       | CDK ECS Patterns        |
| --------------------- | ------------------- | ----------------------- |
| Config format         | TOML (declarative)  | Code (imperative)       |
| Deployment strategies | Rolling, blue/green | Rolling, linear, canary |
| Config separation     | Yes (3-layer)       | No (code-based)         |
| Migrations            | Yes                 | No                      |

**When to use CDK:** TypeScript/Python infrastructure team, need advanced deployment strategies.

______________________________________________________________________

### Pulumi

**URL:** https://www.pulumi.com/

Infrastructure as code using programming languages.

**What it does:**

- Define infrastructure in TypeScript, Python, Go, C#, Java
- ECS support through AWS provider
- State management with cloud backend

**Comparison:**

Similar trade-offs to CDK - code-based configuration vs declarative. Pulumi offers more language choices but requires programming for infrastructure.

**When to use Pulumi:** Multi-cloud needs, preference for general-purpose programming languages over HCL.

______________________________________________________________________

### Kamal

**URL:** https://kamal-deploy.org/

Zero-downtime deployments to VMs using Docker and Traefik. Formerly MRSK.

**What it does:**

- Deploys Docker containers directly to VMs via SSH
- Zero-downtime deployments with Traefik reverse proxy
- Auto-provisions Ubuntu servers with Docker
- Works across cloud providers or bare metal
- Originally built for Rails, supports any containerized app

**Configuration:** Single deploy.yml file

```yaml
service: myapp
image: myapp
servers:
  - 192.168.0.1
  - 192.168.0.2
registry:
  username: user
  password:
    - KAMAL_REGISTRY_PASSWORD
```

**Comparison:**

| Feature           | This Deployer          | Kamal                       |
| ----------------- | ---------------------- | --------------------------- |
| Target            | ECS Fargate            | VMs (any provider)          |
| Orchestrator      | AWS ECS                | Docker + Traefik            |
| Load balancer     | AWS ALB                | Traefik                     |
| Auto-scaling      | Yes (ECS)              | Manual (add servers)        |
| Config separation | Yes (3-layer)          | No (single YAML)            |
| Managed services  | Yes (RDS, ElastiCache) | No (run on VMs or external) |

**When to use Kamal:** Non-AWS, VM-based infrastructure, simpler ops model, avoiding cloud vendor lock-in.

______________________________________________________________________

### PaaS Platforms (Render, Fly.io, Railway)

**URLs:** https://render.com/, https://fly.io/, https://railway.com/

Fully managed container hosting platforms.

**What they do:**

- Git-push deployments
- Built-in databases, caching, background workers
- Managed SSL, domains, scaling

**Comparison:**

These abstract away ALL infrastructure. You lose control but gain simplicity. Generally higher cost at scale, but lower ops burden.

**When to use PaaS:** Small teams, rapid prototyping, minimal infrastructure expertise, acceptable vendor lock-in.

______________________________________________________________________

## Kubernetes Architecture Comparison

For teams evaluating Kubernetes, here's how this deployer's architecture maps to Kubernetes patterns:

| Deployer Concept            | Kubernetes Equivalent                         |
| --------------------------- | --------------------------------------------- |
| deploy.toml (app structure) | Helm chart templates / Kustomize base         |
| config.toml (env bridge)    | values.yaml / Kustomize overlays              |
| terraform.tfvars (sizing)   | HPA configs / resource limits in values       |
| ECS services                | Kubernetes Deployments                        |
| ALB + target groups         | Ingress + Services                            |
| RDS module                  | CloudNativePG / managed DB (RDS, Cloud SQL)   |
| ElastiCache module          | Redis Operator / managed cache                |
| Service discovery           | K8s DNS (service.namespace.svc.cluster.local) |
| Migrations as ECS tasks     | Kubernetes Jobs (Helm pre-install hooks)      |
| Shared environments         | Namespace isolation in shared cluster         |

### Why the Patterns Align

Both this deployer and Kubernetes best practices share a philosophy:

- **Separate "what to run" from "how big to run it"** - app definition vs resource limits
- **Declarative configuration** - desired state, not imperative scripts
- **Environment-specific overrides** - base config + per-env customization
- **Infrastructure as code** - version-controlled, reproducible

### What a Kubernetes Port Would Require

From an architectural perspective (not a migration guide):

1. **Replace deploy.py** with Helm chart generator or Kustomize structure
1. **Replace OpenTofu modules** with Kubernetes manifests (Deployments, Services, Ingress)
1. **Handle migrations** via Kubernetes Job resources (Helm pre-install hooks)
1. **Different infrastructure provisioning** - EKS/GKE cluster setup, managed services integration
1. **Service mesh** considerations for internal service communication

The three-layer separation would translate to:

- **Helm chart templates** = deploy.toml equivalent
- **values.yaml per environment** = config.toml equivalent
- **Resource limits and HPA configs** = terraform.tfvars equivalent

______________________________________________________________________

## Feature Comparison Matrix

| Feature                  | This Deployer | Copilot        | ecspresso | ECS Compose-X  | CDK            | Kamal   |
| ------------------------ | ------------- | -------------- | --------- | -------------- | -------------- | ------- |
| Build images             | Yes           | Yes            | No        | Limited        | Limited        | Yes     |
| Provision infrastructure | Yes           | Yes            | No        | Yes            | Yes            | No      |
| Deploy to ECS            | Yes           | Yes            | Yes       | Yes            | Yes            | No      |
| Migrations               | Yes           | No             | No        | No             | No             | No      |
| Config separation        | 3-layer       | Single         | Partial   | Single         | Code           | Single  |
| Shared environments      | Yes           | No             | No        | No             | No             | No      |
| Blue/green               | ECS native    | Yes            | Yes       | Yes            | Yes            | Traefik |
| IaC backend              | OpenTofu      | CloudFormation | N/A       | CloudFormation | CloudFormation | N/A     |
| Learning curve           | Medium        | Low            | Low       | Medium         | High           | Low     |

______________________________________________________________________

## Summary

### What's Unique About This Deployer

1. **Three-layer configuration separation** - Clear ownership between app structure, infrastructure references, and sizing
1. **Shared environments** - Multiple apps can share VPC, NAT Gateway, ALB, and ECS cluster for cost savings
1. **Integrated migrations** - First-class support for running migrations as ECS tasks before deployment
1. **OpenTofu/Terraform-based** - Full infrastructure control, not locked to CloudFormation
1. **Migration optimization** - Hash-based skip when migrations haven't changed

### When This Tool Fits

- OpenTofu/Terraform shops that want ECS deployments integrated with their IaC workflow
- Teams that want clear separation between app definition and infrastructure sizing
- Organizations deploying multiple apps with shared infrastructure needs
- Django/Rails apps that need migration orchestration

### When Other Tools Fit

- **AWS Copilot** - Quick start, AWS-blessed, less infrastructure control needed
- **ecspresso** - Already have ECS infrastructure, just need deployment
- **Kamal** - Non-AWS, VM-based, avoiding cloud vendor lock-in
- **CDK/Pulumi** - Prefer code over configuration, need advanced deployment strategies
- **PaaS** - Minimal ops burden more important than control or cost

______________________________________________________________________

## Sources

- [AWS Copilot CLI](https://aws.github.io/copilot-cli/)
- [ecspresso](https://github.com/kayac/ecspresso)
- [ECS Compose-X](https://docs.compose-x.io/)
- [fabfuel/ecs-deploy](https://github.com/fabfuel/ecs-deploy)
- [terraform-aws-modules/ecs/aws](https://registry.terraform.io/modules/terraform-aws-modules/ecs/aws/latest)
- [AWS CDK ECS Patterns](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_ecs_patterns-readme.html)
- [Pulumi](https://www.pulumi.com/)
- [Kamal](https://kamal-deploy.org/)
- [Render](https://render.com/)
- [Fly.io](https://fly.io/)
- [Railway](https://railway.com/)
