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

## Initial Account Setup

These steps require Administrator access and only need to be done once.

### 1. Configure Admin Access

**Why:** The setup steps below create IAM resources, which requires administrator-level permissions. You'll configure a dedicated least-privilege user during this process, but the initial setup itself needs admin access.

If you already have an AWS CLI profile with admin access, set it for the duration of this guide:

```bash
export AWS_PROFILE=your-admin-profile
```

If you don't have one yet, create one by adding your admin credentials to `~/.aws/credentials` and a profile to `~/.aws/config`. See [AWS CLI documentation](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html) for details.

Verify it works:

```bash
aws sts get-caller-identity
```

### 2. Configure Deployer

**Why:** The deployer stores environment configurations (terraform files, config.toml) in a separate directory outside this repository. This must be configured before running any init commands.

```bash
cp .env.example .env
```

Edit `.env` and set `DEPLOYER_ENVIRONMENTS_DIR` to where you want environment configs stored (e.g., `~/deployer-environments`). The directory will be created automatically when you run bootstrap.

### 3. Create Route 53 Hosted Zone

**Why:** Every environment needs a domain for HTTPS access and TLS certificate validation. Route 53 hosted zones must exist before any environment infrastructure can be created, because environments reference the zone ID to create DNS records and validate ACM certificates automatically. This can't be automated because most accounts already have a hosted zone, and DNS delegation is account-specific.

If you don't already have a Route 53 hosted zone for your domain:

```bash
aws route53 create-hosted-zone \
  --name example.com \
  --caller-reference "initial-setup-$(date +%s)"
```

Note the hosted zone ID from the output — you'll need it for environment configuration.

### 4. Create an IAM User for Deployer

**Why:** The deployer uses a dedicated IAM user with minimal permissions. This user is **not an administrator** — its only power is assuming three scoped roles that the bootstrap step creates. This separation means day-to-day deployment operations never use admin credentials, and the deployer can only access resources matching your project prefixes.

```bash
aws iam create-user --user-name deployer
aws iam create-access-key --user-name deployer
```

Save the access key ID and secret access key securely. Add them to `~/.aws/credentials`:

```ini
[deployer]
aws_access_key_id = YOUR_ACCESS_KEY
aws_secret_access_key = YOUR_SECRET_KEY
```

### 5. Run Bootstrap

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
cd $DEPLOYER_ENVIRONMENTS_DIR/bootstrap-staging
tofu init
tofu apply
```

Then enable the S3 backend so state is stored remotely:

```bash
uv run python bin/init.py bootstrap --migrate-state bootstrap-staging
cd $DEPLOYER_ENVIRONMENTS_DIR/bootstrap-staging
tofu init -migrate-state
```

Answer "yes" to copy state to S3. Verify with `tofu plan` — it should show "No changes."

### 6. Configure AWS CLI Profiles

**Why:** The deployer scripts automatically select the right AWS profile for each operation (deploy, infrastructure, Cognito) by reading `config.toml`. These profiles tell the AWS CLI to assume the correct role. Without them, you'd have to manually specify `--role-arn` on every command.

Generate the profile entries automatically:

```bash
uv run python bin/init.py setup-profiles
```

This detects your account ID, generates the profile blocks, and offers to append them to `~/.aws/config`.

### 7. Verify Everything

Run the full verification to confirm tools and AWS access are working:

```bash
uv run python bin/init.py verify
```

This checks all tool versions, confirms `DEPLOYER_ENVIRONMENTS_DIR` is set, verifies the bootstrap directory exists, and tests that each AWS profile can successfully assume its role.

## Removing Administrator Access

**Why:** Admin access was only needed for the one-time bootstrap setup. Going forward, all operations use the scoped deployer roles, which are restricted to your configured project prefixes. Removing admin access ensures that deployment operations can't accidentally modify IAM policies or access resources outside the deployer's scope.

Once you've verified all roles work correctly, remove AdministratorAccess from any existing IAM user.

## Re-granting Administrator Access

You'll need admin access again in two situations:

- **Adding a new application** to the deployer (see [Adding New Applications](#adding-new-applications) below)
- **Modifying the deployer IAM policies** themselves (changing what the roles can do)

To temporarily restore admin access, set `AWS_PROFILE` to your admin profile before running the relevant commands. Remove or unset it when you're done.

## Adding New Applications

The deployer uses **project prefixes** in its IAM policies to control which AWS resources each role can access. When you deploy a new application for the first time, its name must be registered as a project prefix so the deployer roles have permission to manage its resources (ECS clusters, ECR repositories, S3 buckets, etc.).

You do **not** need to do this when creating a new staging or production copy of an application that's already registered — only when deploying an application with a new name.

1. **Edit bootstrap configuration**:

   Open `$DEPLOYER_ENVIRONMENTS_DIR/bootstrap-staging/terraform.tfvars` and add the application name to `project_prefixes`:

   ```hcl
   project_prefixes = ["myapp", "otherapp", "newapp"]  # add your new app name
   ```

2. **Apply the changes** (requires admin access):

   ```bash
   cd $DEPLOYER_ENVIRONMENTS_DIR/bootstrap-staging
   tofu plan
   tofu apply
   ```

   This updates the IAM policies and permissions boundary to include the new application's resource patterns.

3. **Proceed to deploy** — follow [DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md) to create the environment and deploy.

## Multi-Account Setup

For staging and production in different AWS accounts, run `init.py bootstrap` once per account:

```bash
# Run once for each account (prompts for account-specific values)
uv run python bin/init.py bootstrap
```

```
$DEPLOYER_ENVIRONMENTS_DIR/
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
