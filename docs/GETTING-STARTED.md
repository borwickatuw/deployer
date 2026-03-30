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
python3 --version    # Should be 3.12 or higher
uv --version
tofu --version
aws --version
docker --version
```

**Additional requirements:**

- AWS account with Administrator access (for initial setup only)
- AWS CLI configured (`aws configure`)

## Initial Account Setup

These steps require Administrator access and only need to be done once.

### 1. Create Route 53 Hosted Zone

If you don't already have a Route 53 hosted zone for your domain:

```bash
aws route53 create-hosted-zone \
  --name example.com \
  --caller-reference "initial-setup-$(date +%s)"
```

Note the hosted zone ID from the output - you'll need it for environment configuration.

### 2. Create an IAM User for Deployer

Create an IAM user that will assume the deployer roles:

```bash
aws iam create-user --user-name deployer

# Create access keys
aws iam create-access-key --user-name deployer
```

Save the access key ID and secret access key securely.

### 3. Configure Bootstrap for Your Account

The bootstrap terraform creates all IAM roles, policies, and shared resources (S3 state bucket, ECS permissions boundary).

Create a bootstrap instance for your account using the init tool:

```bash
uv run python bin/init.py bootstrap
```

This will interactively prompt for your AWS account ID, region, project prefixes,
and trusted IAM user ARNs, then generate the bootstrap directory (e.g., `bootstrap-staging/`).

### 4. Apply Bootstrap Infrastructure

Follow the instructions printed by `init.py bootstrap`:

```bash
cd ~/deployer-environments/bootstrap-staging
AWS_PROFILE=admin tofu init
AWS_PROFILE=admin tofu apply
```

Then enable the S3 backend:

```bash
uv run python bin/init.py bootstrap --migrate-state bootstrap-staging
cd ~/deployer-environments/bootstrap-staging
AWS_PROFILE=admin tofu init -migrate-state
```

This creates:

- S3 bucket for terraform state
- ECS role permissions boundary
- `deployer-app-deploy` role (for deploy.py)
- `deployer-infra-admin` role (for OpenTofu)
- `deployer-cognito-admin` role (for Cognito management)

**Note:** If you have existing IAM resources from a previous setup, run the import script first:

```bash
AWS_PROFILE=admin ./import-existing.sh
```

### 5. Allow the IAM User to Assume Roles

Create a policy that lets the deployer user assume the roles:

```bash
# Get your account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

cat > /tmp/assume-deployer-roles.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": [
        "arn:aws:iam::${ACCOUNT_ID}:role/deployer-app-deploy",
        "arn:aws:iam::${ACCOUNT_ID}:role/deployer-infra-admin",
        "arn:aws:iam::${ACCOUNT_ID}:role/deployer-cognito-admin"
      ]
    }
  ]
}
EOF

aws iam put-user-policy \
  --user-name deployer \
  --policy-name assume-deployer-roles \
  --policy-document file:///tmp/assume-deployer-roles.json
```

### 6. Configure AWS CLI Profiles

Add credentials to `~/.aws/credentials`:

```ini
[deployer]
aws_access_key_id = YOUR_ACCESS_KEY
aws_secret_access_key = YOUR_SECRET_KEY
```

Add profiles to `~/.aws/config` (replace `ACCOUNT_ID` with your actual account ID):

```ini
[profile deployer]
region = us-west-2
output = json

[profile deployer-app]
role_arn = arn:aws:iam::ACCOUNT_ID:role/deployer-app-deploy
source_profile = deployer
region = us-west-2

[profile deployer-infra]
role_arn = arn:aws:iam::ACCOUNT_ID:role/deployer-infra-admin
source_profile = deployer
region = us-west-2

[profile deployer-cognito]
role_arn = arn:aws:iam::ACCOUNT_ID:role/deployer-cognito-admin
source_profile = deployer
region = us-west-2
```

### 7. Configure Deployer Environment

Copy the environment template:

```bash
cp .env.example .env
```

The `.env` file configures the environments directory path. AWS profiles are configured per-environment in each `config.toml` file.

### 8. Test the Roles

Verify each role works:

```bash
# Test app deploy role
AWS_PROFILE=deployer-app aws sts get-caller-identity

# Test infra admin role
AWS_PROFILE=deployer-infra aws sts get-caller-identity

# Test cognito admin role
AWS_PROFILE=deployer-cognito aws sts get-caller-identity
```

Each should show the assumed role ARN.

## Removing Administrator Access

Once you've verified all roles work correctly:

1. **Remove AdministratorAccess** from any existing IAM user
1. **Document** that admin access is only needed for:
   - Modifying the deployer IAM policies themselves
   - Adding new projects (requires updating ARN patterns)
   - Creating the initial IAM infrastructure

## Adding New Projects

When you need to add a new project (not just a new environment of an existing project):

1. **Update bootstrap configuration**:

   - Edit your bootstrap instance's `terraform.tfvars`
   - Add the project name to `project_prefixes`

1. **Apply the changes**:

   ```bash
   cd ~/deployer-environments/bootstrap-staging
   AWS_PROFILE=admin tofu plan
   AWS_PROFILE=admin tofu apply
   ```

   This updates:

   - IAM policies with the new project ARN patterns
   - ECS permissions boundary

1. **Create the environment** - ECR repositories are created automatically when you run `tofu apply` (via `ecr_repository_names` in terraform.tfvars)

## Multi-Account Setup

For staging and production in different AWS accounts, run `init.py bootstrap` once per account:

```bash
# Run once for each account (prompts for account-specific values)
uv run python bin/init.py bootstrap   # creates bootstrap-staging/
uv run python bin/init.py bootstrap   # creates bootstrap-production/
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
