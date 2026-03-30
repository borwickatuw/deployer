# Getting Started

This guide covers one-time AWS account setup for the deployer. After completing this guide, you'll have a secure IAM configuration with least-privilege roles.

For deploying applications, see [DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md).

## Prerequisites

| Tool     | Version | Installation               | Purpose                |
| -------- | ------- | -------------------------- | ---------------------- |
| Python   | 3.12+   | `brew install python@3.12` | Deploy scripts         |
| uv       | Latest  | `brew install uv`          | Python package manager |
| OpenTofu | 1.6+    | `brew install opentofu`    | Infrastructure as code |
| AWS CLI  | v2      | `brew install awscli`      | AWS operations         |
| Docker   | Latest  | `brew install docker`      | Container builds       |

**Verify your setup:**

```bash
uv run python bin/init.py verify
```

This checks all tool versions and (once configured) AWS profile access.

**Additional requirements:**

- AWS account with Administrator access (for initial setup only — see Step 7)
- AWS CLI configured (`aws configure`)

## Initial Account Setup

These steps require Administrator access and only need to be done once.

### 1. Create Route 53 Hosted Zone

**Why:** Every environment needs a domain for HTTPS access and TLS certificate validation. Route 53 hosted zones must exist before any environment infrastructure can be created, because environments reference the zone ID to create DNS records and validate ACM certificates automatically. This can't be automated because most accounts already have a hosted zone, and DNS delegation is account-specific.

If you don't already have a Route 53 hosted zone for your domain:

```bash
aws route53 create-hosted-zone \
  --name example.com \
  --caller-reference "initial-setup-$(date +%s)"
```

Note the hosted zone ID from the output — you'll need it for environment configuration.

### 2. Create an IAM User for Deployer

**Why:** The deployer uses a dedicated IAM user with minimal permissions. This user is **not an administrator** — its only power is assuming three scoped roles that the bootstrap step creates. This separation means day-to-day deployment operations never use admin credentials, and the deployer can only access resources matching your project prefixes.

You need admin access *to create* this user, but the user itself is intentionally unprivileged.

```bash
aws iam create-user --user-name deployer
```

```bash
aws iam create-access-key --user-name deployer
```

Save the access key ID and secret access key securely. Add them to `~/.aws/credentials`:

```ini
[deployer]
aws_access_key_id = YOUR_ACCESS_KEY
aws_secret_access_key = YOUR_SECRET_KEY
```

### 3. Run Bootstrap

**Why:** Bootstrap creates the foundational resources that all environments depend on:

- **S3 bucket** for storing OpenTofu state (so infrastructure state isn't just on your laptop)
- **Permissions boundary** that caps what any ECS task role can do, regardless of what permissions it's given
- **Three IAM roles** scoped to specific operations:
  - `deployer-app-deploy` — deploy containers (used by `deploy.py`)
  - `deployer-infra-admin` — manage infrastructure (used by `tofu.sh`)
  - `deployer-cognito-admin` — manage user pools (used by `cognito.py`)
- **Assume-role policy** on the deployer IAM user, granting it permission to use these three roles

Generate the bootstrap configuration interactively:

```bash
uv run python bin/init.py bootstrap
```

This prompts for your AWS account ID, region, project prefixes, and the deployer user ARN. After generating the files, it offers to run `tofu init && tofu apply` for you.

If you decline the interactive apply, run manually:

```bash
cd ~/deployer-environments/bootstrap-staging
AWS_PROFILE=admin tofu init
```

```bash
AWS_PROFILE=admin tofu apply
```

Then enable the S3 backend so state is stored remotely:

```bash
uv run python bin/init.py bootstrap --migrate-state bootstrap-staging
```

```bash
cd ~/deployer-environments/bootstrap-staging
AWS_PROFILE=admin tofu init -migrate-state
```

Answer "yes" to copy state to S3. Verify with `AWS_PROFILE=admin tofu plan` — it should show "No changes."

**Note:** If you have existing IAM resources from a previous setup, run the import script first:

```bash
AWS_PROFILE=admin ./import-existing.sh
```

### 4. Configure AWS CLI Profiles

**Why:** The deployer scripts automatically select the right AWS profile for each operation (deploy, infrastructure, Cognito) by reading `config.toml`. These profiles tell the AWS CLI to assume the correct role. Without them, you'd have to manually specify `--role-arn` on every command.

Generate the profile entries automatically:

```bash
uv run python bin/init.py setup-profiles
```

This detects your account ID, generates the profile blocks, and offers to append them to `~/.aws/config`.

### 5. Configure Deployer Environment

Copy the environment template:

```bash
cp .env.example .env
```

The `.env` file configures the environments directory path. AWS profiles are configured per-environment in each `config.toml` file.

### 6. Verify Everything

Run the full verification to confirm tools and AWS access are working:

```bash
uv run python bin/init.py verify
```

This checks all tool versions and tests that each AWS profile can successfully assume its role.

## Removing Administrator Access

**Why:** Admin access was only needed for the one-time bootstrap setup. Going forward, all operations use the scoped deployer roles, which are restricted to your configured project prefixes. Removing admin access ensures that deployment operations can't accidentally modify IAM policies or access resources outside the deployer's scope.

Once you've verified all roles work correctly:

1. **Remove AdministratorAccess** from any existing IAM user
1. **Document** that admin access is only needed for:
   - Modifying the deployer IAM policies themselves
   - Adding new projects (requires updating ARN patterns)

## Adding New Projects

When you need to add a new project (not just a new environment of an existing project):

1. **Update bootstrap configuration**:

   - Edit your bootstrap instance's `terraform.tfvars`
   - Add the project name to `project_prefixes`

1. **Apply the changes**:

   ```bash
   cd ~/deployer-environments/bootstrap-staging
   AWS_PROFILE=admin tofu plan
   ```

   ```bash
   AWS_PROFILE=admin tofu apply
   ```

   This updates:

   - IAM policies with the new project ARN patterns
   - ECS permissions boundary

1. **Create the environment** — ECR repositories are created automatically when you run `tofu apply` (via `ecr_repository_names` in terraform.tfvars)

## Multi-Account Setup

For staging and production in different AWS accounts, run `init.py bootstrap` once per account:

```bash
# Run once for each account (prompts for account-specific values)
uv run python bin/init.py bootstrap
```

```
deployer-environments/
├── bootstrap-staging/            # Staging account instance
│   ├── main.tf
│   └── terraform.tfvars          # Staging-specific config
└── bootstrap-production/         # Production account instance
    ├── main.tf
    └── terraform.tfvars          # Production-specific config
```

Each environment specifies its AWS profiles in `config.toml`. See [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md#aws) for full documentation.

```toml
[aws]
deploy_profile = "deployer-app-production"
infra_profile = "deployer-infra-production"
cognito_profile = "deployer-cognito-production"
```

The deployer scripts automatically read the appropriate profile from the environment's config.toml.

## Next Steps

Once your AWS account is configured, proceed to deploy your first application:

- **[DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md)** - Create environments and deploy applications
- [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) - Configuration options
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
