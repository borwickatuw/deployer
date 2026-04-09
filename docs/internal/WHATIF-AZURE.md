# What If Azure?

An analysis of whether this deployer's AWS architecture could be mapped to Azure, covering every AWS service in use, its Azure equivalent, and where the gaps are.

## TL;DR

Most of the stack maps cleanly. Three things don't:

1. **ALB + Cognito auth** -- Azure Application Gateway has no load-balancer-level authentication. You must push auth into the app or add a reverse proxy.
1. **SSM Parameter Store hierarchies** -- Azure Key Vault is flat. No path-based scoping. You need separate vaults per app/environment for access isolation.
1. **Permissions boundaries** -- No Azure equivalent. Must be approximated with custom roles + Azure Policy deny rules.

Everything else has a close or workable equivalent.

______________________________________________________________________

## Service Mapping

### Compute: ECS Fargate --> Azure Container Apps (ACA)

**Match: Close, with one pricing gap**

Azure Container Apps is the right target -- it's serverless containers without managing clusters (ACA runs on AKS under the hood, but you don't touch Kubernetes).

| Feature                         | AWS              | Azure                           | Gap?                   |
| ------------------------------- | ---------------- | ------------------------------- | ---------------------- |
| Serverless containers           | ECS Fargate      | ACA                             | No                     |
| Interactive shell               | ECS Exec         | `az containerapp exec`          | No                     |
| Service discovery               | Cloud Map        | Built-in (ACA internal ingress) | No -- simpler on Azure |
| Multi-container tasks           | Task definitions | Sidecar + init containers       | No                     |
| Scale to zero                   | No               | Yes (default)                   | Azure advantage        |
| **Spot/interruptible capacity** | **Fargate Spot** | **None on ACA**                 | **Yes**                |

**Fargate Spot gap**: ACA has no discounted interruptible tier. Options:

- ACI Spot Containers (different service, no orchestration)
- AKS with Spot node pools (requires managing Kubernetes)
- Accept higher cost on ACA

ACA costs roughly 2-3x more than Fargate at pay-as-you-go rates, though the scale-to-zero capability can offset this for staging environments.

**Why not AKS?** AKS is the EKS equivalent -- you manage the cluster. Only choose AKS if you need Spot node pools, custom operators, or GPU workloads.

______________________________________________________________________

### Networking: VPC --> Azure VNet

**Match: Close**

| Feature          | AWS                    | Azure                      | Notes                                                                                                       |
| ---------------- | ---------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Virtual network  | VPC                    | VNet                       | Direct equivalent                                                                                           |
| Subnets          | Public/private subnets | Subnets + NSGs             | Azure has no inherent public/private distinction; you control with Network Security Groups and route tables |
| NAT Gateway      | NAT Gateway            | Azure NAT Gateway          | Direct equivalent                                                                                           |
| Internet Gateway | IGW resource           | Implicit (public IP + NSG) | No separate resource needed                                                                                 |
| Flow Logs        | VPC Flow Logs          | NSG/VNet Flow Logs         | Direct equivalent                                                                                           |

______________________________________________________________________

### Load Balancing: ALB --> Azure Application Gateway

**Match: Partial -- major gap around auth**

| Feature                        | AWS                | Azure                    | Gap?             |
| ------------------------------ | ------------------ | ------------------------ | ---------------- |
| Path-based routing             | ALB listener rules | URL path maps            | No               |
| Health checks                  | Configurable       | Custom probes            | No               |
| Idle timeout                   | Up to 4000s        | Up to 30 min             | No               |
| WAF integration                | AWS WAF v2         | Azure WAF on App Gateway | No               |
| **LB-level Cognito/OIDC auth** | **Native**         | **Does not exist**       | **Yes -- major** |

**The auth gap is the biggest single issue.** AWS ALB natively intercepts requests, redirects unauthenticated users to Cognito, validates tokens, and forwards authenticated requests with user claims in headers. Azure Application Gateway has nothing like this.

Workarounds:

- **oauth2-proxy sidecar**: Run [oauth2-proxy](https://oauth2-proxy.github.io/) as a sidecar container in ACA. Handles OIDC auth before requests reach your app.
- **Azure API Management (APIM)**: Add as a gateway layer with OAuth token validation policies. Heavyweight, adds cost and complexity.
- **App-level auth**: Implement OIDC auth in the application itself. Most frameworks have middleware for this, but it means every app must handle it.

The oauth2-proxy sidecar approach is probably the closest to the current "auth at the infrastructure layer" pattern.

______________________________________________________________________

### Storage: S3 --> Azure Blob Storage

**Match: Close**

| Feature              | AWS                          | Azure                           | Notes                                                      |
| -------------------- | ---------------------------- | ------------------------------- | ---------------------------------------------------------- |
| Object storage       | S3                           | Blob Storage                    | Direct equivalent                                          |
| Versioning           | Bucket versioning            | Blob versioning                 | Close; Azure creates a version on every write when enabled |
| CORS                 | Bucket-level rules           | Service-level rules             | Direct equivalent                                          |
| Encryption           | SSE-S3/SSE-KMS               | Microsoft/customer-managed keys | Direct equivalent                                          |
| Lifecycle policies   | Transition + expiration      | Tier transition + delete rules  | Close                                                      |
| Public access blocks | Account/bucket level         | Account/container level         | Direct equivalent                                          |
| Bucket policies      | Resource-based JSON policies | RBAC + SAS tokens               | Different model but equivalent                             |

______________________________________________________________________

### CDN: CloudFront --> Azure Front Door

**Match: Partial**

| Feature                  | AWS                 | Azure                        | Gap?                                   |
| ------------------------ | ------------------- | ---------------------------- | -------------------------------------- |
| CDN distribution         | CloudFront          | Front Door Standard/Premium  | No                                     |
| Cache policies           | Configurable        | Caching rules                | No                                     |
| WAF integration          | AWS WAF v2          | Azure WAF on Front Door      | No                                     |
| Origin access to storage | OAC                 | Private Link to Blob Storage | No -- different mechanism, same result |
| **Custom error pages**   | **Per status code** | **Not supported**            | **Yes**                                |

The custom error pages gap affects the CloudFront-in-front-of-ALB pattern that serves branded 502/503/504 pages during deployments. On Azure, you'd need to handle these in the application or via a custom middleware.

______________________________________________________________________

### Database: RDS PostgreSQL --> Azure Database for PostgreSQL Flexible Server

**Match: Close**

| Feature                | AWS                    | Azure                     | Notes                         |
| ---------------------- | ---------------------- | ------------------------- | ----------------------------- |
| Managed PostgreSQL     | RDS                    | Flexible Server           | Direct equivalent             |
| Multi-AZ               | Multi-AZ deployment    | Zone-redundant HA         | Direct equivalent             |
| Automated backups      | Up to 35-day retention | Up to 35-day retention    | Direct equivalent             |
| Performance monitoring | Performance Insights   | Query Performance Insight | Close                         |
| Enhanced monitoring    | OS-level metrics       | Azure Monitor metrics     | Integrated into Azure Monitor |
| Manual snapshots       | Snapshots              | On-demand backups         | Direct equivalent             |
| Point-in-time restore  | Yes                    | Yes                       | Direct equivalent             |
| Start/Stop             | Yes                    | Yes                       | Direct equivalent             |

This is one of the cleanest mappings. Very little would need to change architecturally.

______________________________________________________________________

### Caching: ElastiCache Redis --> Azure Cache for Redis

**Match: Close**

| Feature           | AWS           | Azure                    | Notes             |
| ----------------- | ------------- | ------------------------ | ----------------- |
| Single node Redis | Yes           | Basic tier               | Direct equivalent |
| Network isolation | Subnet groups | VNet integration         | Direct equivalent |
| Clustering        | Yes           | Premium/Enterprise tiers | Direct equivalent |

______________________________________________________________________

### Authentication: Cognito --> Microsoft Entra External ID

**Match: Partial**

| Feature                       | AWS                | Azure               | Gap?                                 |
| ----------------------------- | ------------------ | ------------------- | ------------------------------------ |
| User pool / directory         | Cognito User Pools | Entra External ID   | No                                   |
| OAuth/OIDC                    | Yes                | Yes                 | No                                   |
| Admin user management         | Cognito Admin API  | Microsoft Graph API | No -- different API, same capability |
| Password policies             | Yes                | Yes                 | No                                   |
| **LB-level auth integration** | **ALB + Cognito**  | **None**            | **Yes -- see ALB section**           |

Note: Azure AD B2C (the older Cognito equivalent) reached end-of-sale May 2025. New deployments must use Microsoft Entra External ID, which is a different product.

______________________________________________________________________

### IAM: AWS IAM --> Azure RBAC + Entra ID + Managed Identities

**Match: Partial**

| Feature                    | AWS               | Azure                        | Gap?                  |
| -------------------------- | ----------------- | ---------------------------- | --------------------- |
| Service roles              | IAM Roles         | Managed Identities           | No                    |
| Policy documents           | JSON policies     | RBAC role definitions        | No -- different model |
| Service-linked roles       | Yes               | Built-in roles               | No                    |
| OIDC federation            | IAM OIDC Provider | Workload Identity Federation | No                    |
| **Permissions boundaries** | **Yes**           | **No equivalent**            | **Yes**               |

**Permissions boundaries gap**: In this codebase, permissions boundaries enforce a ceiling on what delegated roles can do (e.g., CI roles can't escalate beyond their boundary). Azure has no single equivalent. You'd need to combine:

- Hierarchical RBAC scoping (Management Group > Subscription > Resource Group)
- Azure Policy deny rules to prevent specific actions
- Custom role definitions that limit allowed actions

This is more complex to set up and reason about than a single boundary policy.

______________________________________________________________________

### Secrets: Secrets Manager + SSM Parameter Store --> Azure Key Vault

**Match: Partial -- significant architectural difference**

| Feature                       | AWS                              | Azure                           | Gap?                                |
| ----------------------------- | -------------------------------- | ------------------------------- | ----------------------------------- |
| Database credentials          | Secrets Manager                  | Key Vault secrets               | No                                  |
| App secrets (SecureString)    | SSM Parameter Store              | Key Vault secrets               | No                                  |
| Container secrets injection   | ECS `valueFrom` SSM path         | ACA `keyvaultref:` syntax       | No -- different syntax, same result |
| **Hierarchical paths**        | **`/myapp/staging/DB_PASSWORD`** | **Flat namespace**              | **Yes**                             |
| **Path-based access control** | **IAM on `/myapp/staging/*`**    | **Per-vault RBAC only**         | **Yes**                             |
| Cost                          | SSM Standard is free             | Key Vault charges per operation | Minor                               |

**The hierarchy gap matters.** This codebase uses SSM paths like `/myapp/staging/SECRET_KEY` and scopes IAM access by path prefix. Azure Key Vault has a flat namespace -- you can simulate hierarchy with naming conventions (`myapp-staging-SECRET-KEY`) but can't scope RBAC access to a prefix within a vault. You must use separate Key Vaults per app/environment for equivalent isolation, which changes the infrastructure topology.

For non-secret configuration, Azure App Configuration is the recommended service, with Key Vault references for secrets. This splits what SSM does into two services.

______________________________________________________________________

### Monitoring: CloudWatch --> Azure Monitor

**Match: Close**

| Feature              | AWS                   | Azure                        | Notes                         |
| -------------------- | --------------------- | ---------------------------- | ----------------------------- |
| Log Groups / Streams | CloudWatch Logs       | Log Analytics workspace      | Direct equivalent             |
| Query language       | Insights query syntax | Kusto Query Language (KQL)   | KQL is arguably more powerful |
| Alarms               | CloudWatch Alarms     | Azure Monitor Alerts         | Direct equivalent             |
| Container metrics    | Container Insights    | Azure Monitor for containers | Direct equivalent             |

______________________________________________________________________

### Scheduling: EventBridge --> Azure Functions Timer Trigger

**Match: Close**

| Feature               | AWS               | Azure                | Notes             |
| --------------------- | ----------------- | -------------------- | ----------------- |
| Cron-based scheduling | EventBridge rules | Timer trigger (cron) | Direct equivalent |
| Target                | Lambda            | Azure Function       | Direct equivalent |
| Event bus             | EventBridge bus   | Azure Event Grid     | Direct equivalent |

______________________________________________________________________

### DNS: Route 53 --> Azure DNS

**Match: Close**

| Feature                    | AWS              | Azure                     | Notes                       |
| -------------------------- | ---------------- | ------------------------- | --------------------------- |
| Hosted zones               | Yes              | DNS Zones                 | Direct equivalent           |
| Alias records              | To AWS resources | To Azure resources        | Direct equivalent           |
| Health-check-based routing | Built-in         | Via Azure Traffic Manager | Requires additional service |

______________________________________________________________________

### Certificates: ACM --> Azure Managed Certificates

**Match: Close**

| Feature                    | AWS          | Azure                                     | Notes                              |
| -------------------------- | ------------ | ----------------------------------------- | ---------------------------------- |
| Free public certs          | ACM          | Managed certs on App Gateway / Front Door | Free when used with Azure services |
| DNS validation             | CNAME record | Supported                                 | Direct equivalent                  |
| Auto-renewal               | Yes          | Yes                                       | Direct equivalent                  |
| Standalone cert management | ACM          | Azure Key Vault Certificates              | Different service, same result     |

______________________________________________________________________

### WAF: AWS WAF v2 --> Azure WAF

**Match: Close**

| Feature       | AWS                    | Azure                       | Notes             |
| ------------- | ---------------------- | --------------------------- | ----------------- |
| Managed rules | AWS Managed Rules      | OWASP CRS + Microsoft rules | Direct equivalent |
| Rate limiting | Per IP                 | Per IP, geography, etc.     | Direct equivalent |
| Geo-blocking  | Yes                    | Geomatch custom rules       | Direct equivalent |
| IP allowlists | Yes                    | Custom rules                | Direct equivalent |
| Bot control   | Bot Control rule group | Bot protection rule set     | Direct equivalent |

______________________________________________________________________

### Container Registry: ECR --> Azure Container Registry (ACR)

**Match: Close**

| Feature            | AWS                  | Azure                     | Notes                       |
| ------------------ | -------------------- | ------------------------- | --------------------------- |
| Image storage      | ECR                  | ACR                       | Direct equivalent           |
| Auth               | `get-login-password` | `az acr login`            | Direct equivalent           |
| Image scanning     | Basic (free)         | Microsoft Defender (paid) | Cost difference             |
| Lifecycle policies | Declarative rules    | `acr purge` tasks         | Less elegant but functional |

______________________________________________________________________

### Serverless Functions: Lambda --> Azure Functions

**Match: Close**

| Feature              | AWS                 | Azure                           | Notes             |
| -------------------- | ------------------- | ------------------------------- | ----------------- |
| Serverless execution | Lambda              | Azure Functions                 | Direct equivalent |
| VPC attachment       | Yes                 | VNet integration (Premium plan) | Direct equivalent |
| Scheduled invocation | EventBridge trigger | Timer trigger                   | Direct equivalent |

______________________________________________________________________

### CI/CD: GitHub OIDC --> Azure Workload Identity Federation

**Match: Close**

| Feature               | AWS                                     | Azure                                | Notes             |
| --------------------- | --------------------------------------- | ------------------------------------ | ----------------- |
| Credential-less CI/CD | IAM OIDC Provider                       | Workload Identity Federation         | Direct equivalent |
| GitHub Actions action | `aws-actions/configure-aws-credentials` | `azure/login`                        | Direct equivalent |
| Repo/branch scoping   | Trust policy conditions                 | Federated credential subject filters | Direct equivalent |

______________________________________________________________________

### Right-Sizing: Compute Optimizer --> Azure Advisor

**Match: Partial**

AWS Compute Optimizer provides ECS Fargate task sizing recommendations. Azure Advisor provides VM and AKS rightsizing but has no equivalent for ACA containers. You'd need to build your own analysis from Azure Monitor metrics.

______________________________________________________________________

## OpenTofu/Terraform Provider Maturity

The `azurerm` provider is production-grade with weekly releases, co-maintained by HashiCorp and Microsoft. Coverage for the services needed here (VNet, ACA, PostgreSQL, Key Vault, ACR, App Gateway, WAF, DNS, Entra ID) is solid.

The AWS provider has ~7-8x more total downloads, reflecting a larger community, but the Azure provider is not immature -- it's been around since 2016 with 1,100+ resource types.

All the OpenTofu modules in this codebase would need to be rewritten against `azurerm` resources. The module structure and patterns would transfer, but the resource definitions are completely different.

______________________________________________________________________

## Effort Estimate by Component

| Component                                    | Effort   | Notes                                                            |
| -------------------------------------------- | -------- | ---------------------------------------------------------------- |
| VPC/VNet, subnets, NAT                       | Low      | Straightforward mapping                                          |
| ECS --> ACA                                  | Medium   | Different resource model, different config surface               |
| ALB --> App Gateway                          | Medium   | Path routing maps; auth does not                                 |
| ALB + Cognito auth                           | **High** | Requires architectural rethink (oauth2-proxy or app-level)       |
| RDS --> Azure PostgreSQL                     | Low      | Very clean mapping                                               |
| ElastiCache --> Azure Redis                  | Low      | Clean mapping                                                    |
| S3 --> Blob Storage                          | Low      | Clean mapping                                                    |
| CloudFront --> Front Door                    | Medium   | Custom error pages gap                                           |
| SSM --> Key Vault                            | **High** | Hierarchy/scoping requires per-env vaults, code changes          |
| Secrets Manager --> Key Vault                | Low      | Clean mapping                                                    |
| IAM --> RBAC + Managed Identities            | **High** | Different model, no permissions boundaries                       |
| Cognito --> Entra External ID                | Medium   | Different API surface, different product                         |
| CloudWatch --> Azure Monitor                 | Medium   | Different query language (KQL), different API                    |
| WAF                                          | Low      | Clean mapping                                                    |
| ECR --> ACR                                  | Low      | Clean mapping                                                    |
| Route 53 --> Azure DNS                       | Low      | Clean mapping                                                    |
| ACM --> Managed certs                        | Low      | Clean mapping                                                    |
| Lambda --> Azure Functions                   | Low      | Clean mapping                                                    |
| EventBridge --> Timer triggers               | Low      | Clean mapping                                                    |
| GitHub OIDC --> Workload Identity Federation | Low      | Clean mapping                                                    |
| Python scripts (boto3 --> azure-sdk)         | **High** | Every script uses boto3; all must be rewritten against azure SDK |

______________________________________________________________________

## Conclusion

About 70% of the AWS services map cleanly to Azure with minimal architectural change. The remaining 30% requires real work:

**Must rearchitect:**

- Authentication at the load balancer (ALB + Cognito --> oauth2-proxy sidecar or app-level auth)
- Secrets hierarchy and scoping (SSM paths --> multiple Key Vaults)
- IAM permissions boundaries (no Azure equivalent; approximate with custom roles + Policy)

**Must rewrite:**

- All OpenTofu modules (AWS resources --> Azure resources)
- All Python scripts (boto3 --> azure-sdk-for-python)
- CI/CD workflows (AWS actions --> Azure actions)

**Transfers as-is:**

- Architecture patterns (VPC isolation, private subnets, NAT for egress)
- Deployment model (register task def --> update service --> wait for stability)
- Config structure (deploy.toml, config.toml, environment directories)
- Operational patterns (start/stop staging, scheduled environments, DB user separation)

The project structure and operational model would survive the port. The infrastructure code and SDK calls would not.

______________________________________________________________________

## Business Impact

### Migration cost

Building `deployer-azure` is not a port -- it's a ground-up rewrite of everything below the deploy.toml layer. The deployer currently has ~380 tests, ~20 Python scripts, ~15 OpenTofu modules, and ~6 environment templates. All of that would need Azure equivalents written, tested, and validated against real deployments.

This is likely **several months of dedicated infrastructure engineering work** before the first app can deploy on Azure. During that time, the AWS deployer still needs maintenance, so the team is effectively supporting two systems with the capacity previously used for one.

### Ongoing compute costs

Azure Container Apps is roughly **2-3x more expensive** than ECS Fargate at pay-as-you-go rates for equivalent compute. There is no equivalent of Fargate Spot (up to 70% discount for interruptible workloads).

ACA's scale-to-zero capability can offset some of this for staging environments that sit idle, but for production workloads that run 24/7, the cost difference is real and ongoing.

### Auth gap affects every application

The ALB + Cognito pattern currently protects staging environments at the infrastructure level -- applications don't need any auth code. Moving to Azure means **every application** must either:

- Run an oauth2-proxy sidecar (infrastructure team must build and maintain this pattern for ACA, and every app gets an extra container)
- Implement OIDC auth in its own code (every app team takes on auth work they didn't have before)

This isn't a one-time migration cost. It's an ongoing burden: every new app deployed on Azure needs auth handling that was previously free on AWS.

### Secrets management complexity

SSM Parameter Store is free (Standard tier) with path-based access control. Azure Key Vault charges per operation and has no path-based scoping, requiring **separate vaults per application per environment** for equivalent access isolation. For N apps across staging and production, that's 2N Key Vaults to provision and manage, versus one SSM namespace with path prefixes.

### Security model differences

Permissions boundaries are the guardrail that makes delegated CI roles safe -- they set a ceiling on what a role can do regardless of attached policies. Without them, the Azure equivalent requires assembling the same guarantees from custom role definitions plus Azure Policy deny rules. This is:

- More complex to set up and audit
- Easier to get wrong (a missing deny rule silently permits escalation)
- More operational overhead when onboarding new projects

### What you keep

The deploy.toml protocol, the three-config-file architecture, the operational playbooks (deployment, monitoring, emergency procedures), and the team's understanding of containerized deployment patterns all transfer. These represent the design investment. The AWS-specific code is the implementation of those designs, and implementations can be rebuilt.

### Summary

| Impact area          | Effect                                                                 |
| -------------------- | ---------------------------------------------------------------------- |
| Upfront engineering  | Months of infrastructure work to build deployer-azure                  |
| Compute cost         | ~2-3x higher on ACA vs Fargate; no spot equivalent                     |
| Per-app auth burden  | Every app needs auth handling that was previously infrastructure-level |
| Secrets management   | More vaults, per-operation costs, more complex access isolation        |
| Security guardrails  | Permissions boundaries must be approximated; harder to audit           |
| Preserved investment | deploy.toml protocol, architecture patterns, operational knowledge     |

______________________________________________________________________

## deploy.toml Portability

The deploy.toml format is almost entirely cloud-agnostic. It describes *what to run*, not *where to run it*.

**Already portable (no changes needed):**

| Section         | Fields                                                                                   | Notes                                               |
| --------------- | ---------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `[application]` | `name`, `description`, `source`                                                          | Pure app metadata                                   |
| `[images.*]`    | `context`, `dockerfile`, `target`, `depends_on`, `push`, `build_args`                    | Docker-native, no cloud coupling                    |
| `[services.*]`  | `image`, `command`, `port`, `health_check_path`, `path_pattern`, `min_cpu`, `min_memory` | Describes services generically                      |
| `[environment]` | All key-value pairs, `${placeholder}` syntax                                             | Cloud-agnostic env vars with placeholder resolution |
| `[migrations]`  | `enabled`, `service`, `command`                                                          | Just "run this command before deploy"               |
| `[commands]`    | Named command arrays                                                                     | Framework-agnostic command definitions              |
| `[database]`    | `type`, `extensions`                                                                     | Declares requirements, not implementations          |

**AWS-specific (two items):**

| Field                               | Issue                                                         | Fix                                                                                                       |
| ----------------------------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| ~~`ecr_prefix` in `[application]`~~ | ~~ECR-specific~~                                              | Removed -- this was vestigial; the deploy pipeline reads it from environment config.toml, not deploy.toml |
| `[secrets]` URI format              | `ssm:/path` and `secretsmanager:name:key` are AWS conventions | Define Azure equivalents: `keyvault:vault-name/secret-name`                                               |

The secrets URI format is the only real design decision. A `deployer-azure` project could define its own secret URI scheme (e.g., `keyvault:myvault/secret-key`) while keeping the rest of deploy.toml identical. An app migrating from AWS to Azure would only need to update `[secrets]`.

The config.toml (environment-level config) is heavily cloud-specific by design -- it's the bridge between infrastructure outputs and the deploy script. A `deployer-azure` project would have its own config.toml structure referencing Azure resources instead of AWS ones.

______________________________________________________________________

## Recommendation: Separate Project, Shared Protocol

This project is effectively `deployer-aws`. If Azure support were needed, the right approach is a separate `deployer-azure` project that shares the deploy.toml protocol -- not an abstraction layer within this codebase.

### Project structure

The deploy.toml is the shared contract between app developers and whichever cloud implementation deploys their app:

```
deploy.toml              <-- shared protocol, lives in app repo
                             cloud-agnostic: images, services, commands, env vars

deployer                 <-- this repo (effectively "deployer-aws")
  bin/                       Python scripts using boto3
  modules/                   OpenTofu modules targeting AWS resources
  templates/                 Templates generating AWS infrastructure

deployer-environments    <-- AWS-specific
  myapp-staging/             tofu configs: ECS, ALB, RDS, SSM, etc.
  myapp-production/

deployer-azure           <-- hypothetical separate project
  bin/                       Python scripts using azure-sdk
  modules/                   OpenTofu modules targeting Azure resources
  templates/                 Templates generating Azure infrastructure

deployer-azure-environments  <-- Azure-specific
  myapp-staging/             tofu configs: ACA, App Gateway, Azure PostgreSQL, Key Vault, etc.
  myapp-production/
```

The app developer's experience stays the same: write a deploy.toml describing services, images, commands, and secrets. Which deployer implementation consumes it is an infrastructure decision, not an app decision.

### What transfers to a new project

- The deploy.toml format (almost verbatim -- app structure is cloud-agnostic)
- The three-config-file design (deploy.toml + tfvars + config.toml)
- The workflow design (how deployments, operations, and emergency procedures are structured)
- The documentation patterns and operational playbooks

### What doesn't (and shouldn't be abstracted)

- OpenTofu modules, Python SDK code, IAM/RBAC models, secrets management

Trying to support both clouds in one codebase leads to a lowest-common-denominator abstraction layer (`ContainerOrchestrator`, `SecretsBackend`, etc.) that can't use the best features of either cloud, makes debugging harder through indirection, and doubles the testing surface for every feature. Each cloud's tooling is idiomatic to that cloud -- fighting that creates complexity for everyone.

### Spec ownership and validation

This repo owns the deploy.toml spec. The authoritative definition is the parser in `src/deployer/config/deploy_config.py` and the reference docs in [CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md). A `deployer-azure` would follow the spec, implementing its own parser.

There is no shared validator enforcing the intersection of both clouds. Each implementation validates what *it* supports and fails fast on anything it doesn't. For example, an Azure deployer seeing `ssm:/path` in `[secrets]` should fail with a clear error ("unsupported secret URI scheme -- use keyvault:vault/secret"), not silently ignore it.

The `[secrets]` section is the main extension point where implementations diverge: each cloud defines its own URI scheme for secret references. Everything else in deploy.toml is already cloud-agnostic.

If a second implementation actually materializes and the parsers start diverging, extracting `deploy_config.py` into a shared package is straightforward -- the module is self-contained with no cloud-specific dependencies.

See [DECISIONS.md](DECISIONS.md) (2026-02-19) for the full reasoning.
