# Multiple AWS Accounts

This guide explains how to deploy environments across multiple AWS accounts, such as having staging in one account and production in another.

This guide assumes you've completed [GETTING-STARTED.md](../GETTING-STARTED.md) for your first account. The steps below cover what's different when adding a second account.

## Why Multiple Accounts?

Separating staging and production into different AWS accounts provides:

- **Blast radius isolation**: A misconfiguration in staging can't affect production resources
- **Compliance**: Some regulations require environment separation
- **Cost tracking**: Clear separation of staging vs production costs
- **IAM isolation**: Staging credentials can't access production resources

## Architecture Overview

```
AWS Account: Staging (111111111111)
├── IAM roles (deployer-app-deploy, deployer-infra-admin, deployer-cognito-admin)
├── S3 bucket (deployer-terraform-state-111111111111)
├── myapp-staging environment
└── otherapp-staging environment

AWS Account: Production (222222222222)
├── IAM roles (deployer-app-deploy, deployer-infra-admin, deployer-cognito-admin)
├── S3 bucket (deployer-terraform-state-222222222222)
├── myapp-production environment
└── otherapp-production environment
```

## Setup Steps

### 1. Create IAM User in the New Account

Follow [GETTING-STARTED.md Step 4](../GETTING-STARTED.md#4-create-an-iam-user-for-deployer) in the new account to create a `deployer` IAM user. Save the credentials.

### 2. Run Bootstrap for the New Account

Run `init.py bootstrap` again — it will prompt for the new account's ID and create a separate bootstrap directory (e.g., `bootstrap-production`):

```bash
uv run python bin/init.py bootstrap
```

The bootstrap module automatically creates the assume-role policy on the IAM user, so no manual policy setup is needed.

Your environments directory will look like:

```
$DEPLOYER_ENVIRONMENTS_DIR/
├── bootstrap-staging/            # Staging account
│   ├── main.tf
│   └── terraform.tfvars
└── bootstrap-production/         # Production account
    ├── main.tf
    └── terraform.tfvars
```

### 3. Configure AWS CLI Profiles

Run `init.py setup-profiles` for the new account. Since you'll have profiles for both accounts, you'll want distinct source profile names (e.g., `deployer-staging` and `deployer-production`).

Add credentials for the new account to `~/.aws/credentials`:

```ini
[deployer-production]
aws_access_key_id = PRODUCTION_ACCESS_KEY
aws_secret_access_key = PRODUCTION_SECRET_KEY
```

Add role profiles to `~/.aws/config`:

```ini
[profile deployer-app-production]
role_arn = arn:aws:iam::222222222222:role/deployer-app-deploy
source_profile = deployer-production
region = us-west-2

[profile deployer-infra-production]
role_arn = arn:aws:iam::222222222222:role/deployer-infra-admin
source_profile = deployer-production
region = us-west-2

[profile deployer-cognito-production]
role_arn = arn:aws:iam::222222222222:role/deployer-cognito-admin
source_profile = deployer-production
region = us-west-2
```

### 4. Configure Environment Profiles

Each environment's `config.toml` specifies which AWS profiles to use. This is the key difference from single-account — environments in different accounts point to different profiles:

**myapp-staging/config.toml:**

```toml
[aws]
deploy_profile = "deployer-app-staging"
infra_profile = "deployer-infra-staging"
cognito_profile = "deployer-cognito-staging"
```

**myapp-production/config.toml:**

```toml
[aws]
deploy_profile = "deployer-app-production"
infra_profile = "deployer-infra-production"
cognito_profile = "deployer-cognito-production"
```

### 5. Test the Setup

Verify each profile works:

```bash
AWS_PROFILE=deployer-app-production aws sts get-caller-identity
AWS_PROFILE=deployer-infra-production aws sts get-caller-identity
```

## Usage

Once configured, the deployer scripts automatically use the correct profile for each environment:

```bash
# Deploys to staging account
uv run python bin/deploy.py myapp-staging

# Deploys to production account
uv run python bin/deploy.py myapp-production

# Infrastructure changes
bin/tofu.sh rollout myapp-staging
bin/tofu.sh rollout myapp-production
```

## Single Account Setup

If you're using a single AWS account for all environments (the default), you only need one bootstrap instance and the default profiles work everywhere. No per-environment `[aws]` section in config.toml is needed.

## Migrating to Multiple Accounts

If you're moving from a single account to multiple accounts:

1. Create the new account and run bootstrap
1. Create new environment directories for the new account
1. Run `tofu apply` to create infrastructure in the new account
1. Migrate data (database dumps, S3 objects) as needed
1. Update DNS to point to the new environment
1. Decommission the old environment

## Troubleshooting

### "Unable to assume role"

Verify:

1. The IAM user exists in the target account
1. The bootstrap has been applied in that account (creates the roles and assume-role policy)
1. The AWS CLI profile is configured correctly

### "Access Denied" on resources

Verify:

1. The project name is in the bootstrap's `project_prefixes`
1. The bootstrap has been applied after adding the project
1. You're using the correct profile for the operation

### Profile not being read from config.toml

Verify:

1. The `[aws]` section exists in config.toml
1. The profile key matches the operation (e.g., `deploy_profile` for deploy.py)
1. There are no syntax errors in config.toml
