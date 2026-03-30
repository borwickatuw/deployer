# Multiple AWS Accounts

This guide explains how to deploy environments across multiple AWS accounts, such as having staging in one account and production in another.

## Why Multiple Accounts?

Separating staging and production into different AWS accounts provides:

- **Blast radius isolation**: A misconfiguration in staging can't affect production resources
- **Cost tracking**: Clear separation of staging vs production costs
- **IAM isolation**: Staging credentials can't access production resources
- **Compliance**: Some regulations require environment separation

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

### 1. Create Bootstrap Instance Per Account

Each AWS account needs its own bootstrap instance to create IAM roles and shared resources.

```
deployer-environments/
├── bootstrap/                    # Shared module (don't modify)
├── bootstrap-staging/            # Staging account
│   ├── main.tf
│   ├── terraform.tfvars
│   └── terraform.tfvars.example
└── bootstrap-production/         # Production account
    ├── main.tf
    ├── terraform.tfvars
    └── terraform.tfvars.example
```

Create a new bootstrap instance:

```bash
cd ~/deployer-environments
cp -r bootstrap-staging bootstrap-production
```

Edit `bootstrap-production/terraform.tfvars`:

```hcl
region = "us-west-2"

project_prefixes = ["myapp", "otherapp"]

# Use the production account's IAM user ARN
trusted_user_arns = ["arn:aws:iam::222222222222:user/deployer"]
```

### 2. Create IAM User in Each Account

Each account needs an IAM user that can assume the deployer roles.

In the **production** account (with admin access):

```bash
aws iam create-user --user-name deployer
aws iam create-access-key --user-name deployer
```

Save the credentials securely.

### 3. Apply Bootstrap to Each Account

Apply the bootstrap in each account using admin credentials:

```bash
# Staging account
cd ~/deployer-environments/bootstrap-staging
AWS_PROFILE=admin-staging tofu init
AWS_PROFILE=admin-staging tofu apply

# Production account
cd ~/deployer-environments/bootstrap-production
AWS_PROFILE=admin-production tofu init
AWS_PROFILE=admin-production tofu apply
```

### 4. Allow IAM Users to Assume Roles

In each account, allow the deployer user to assume the roles:

```bash
# In production account
ACCOUNT_ID=222222222222

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

AWS_PROFILE=admin-production aws iam put-user-policy \
  --user-name deployer \
  --policy-name assume-deployer-roles \
  --policy-document file:///tmp/assume-deployer-roles.json
```

### 5. Configure AWS CLI Profiles

Add credentials for each account to `~/.aws/credentials`:

```ini
[deployer-staging]
aws_access_key_id = STAGING_ACCESS_KEY
aws_secret_access_key = STAGING_SECRET_KEY

[deployer-production]
aws_access_key_id = PRODUCTION_ACCESS_KEY
aws_secret_access_key = PRODUCTION_SECRET_KEY
```

Add role profiles to `~/.aws/config`:

```ini
# Staging account profiles
[profile deployer-app-staging]
role_arn = arn:aws:iam::111111111111:role/deployer-app-deploy
source_profile = deployer-staging
region = us-west-2

[profile deployer-infra-staging]
role_arn = arn:aws:iam::111111111111:role/deployer-infra-admin
source_profile = deployer-staging
region = us-west-2

[profile deployer-cognito-staging]
role_arn = arn:aws:iam::111111111111:role/deployer-cognito-admin
source_profile = deployer-staging
region = us-west-2

# Production account profiles
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

### 6. Configure Environment Profiles

Each environment's `config.toml` specifies which AWS profiles to use:

**myapp-staging/config.toml:**

```toml
[aws]
deploy_profile = "deployer-app-staging"
infra_profile = "deployer-infra-staging"
cognito_profile = "deployer-cognito-staging"

[environment]
type = "staging"
# ... rest of config
```

**myapp-production/config.toml:**

```toml
[aws]
deploy_profile = "deployer-app-production"
infra_profile = "deployer-infra-production"
cognito_profile = "deployer-cognito-production"

[environment]
type = "production"
# ... rest of config
```

### 7. Test the Setup

Verify each profile works:

```bash
# Test staging
AWS_PROFILE=deployer-app-staging aws sts get-caller-identity
AWS_PROFILE=deployer-infra-staging aws sts get-caller-identity

# Test production
AWS_PROFILE=deployer-app-production aws sts get-caller-identity
AWS_PROFILE=deployer-infra-production aws sts get-caller-identity
```

## Usage

Once configured, the deployer scripts automatically use the correct profile for each environment:

```bash
# Link environments to deploy.toml (one-time)
uv run python bin/link-environments.py myapp-staging ../myapp/deploy.toml
uv run python bin/link-environments.py myapp-production ../myapp/deploy.toml

# Deploys to staging account
uv run python bin/deploy.py myapp-staging

# Deploys to production account
uv run python bin/deploy.py myapp-production

# Infrastructure changes to staging account
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging

# Or use rollout to run init, plan, and apply in sequence
./bin/tofu.sh rollout myapp-staging

# Infrastructure changes to production account
./bin/tofu.sh rollout myapp-production
```

## Single Account Setup

If you're using a single AWS account for all environments (the default), you only need one bootstrap instance and can use the same profiles everywhere:

```toml
# All environments use the same profiles
[aws]
deploy_profile = "deployer-app"
infra_profile = "deployer-infra"
cognito_profile = "deployer-cognito"
```

## Migrating to Multiple Accounts

If you're moving from a single account to multiple accounts:

1. Create the new account and bootstrap it
1. Create new environment directories for the new account
1. Run `tofu apply` to create infrastructure in the new account
1. Migrate data (database dumps, S3 objects) as needed
1. Update DNS to point to the new environment
1. Decommission the old environment

## Troubleshooting

### "Unable to assume role"

Verify:

1. The IAM user exists in the target account
1. The user has the `assume-deployer-roles` policy attached
1. The role's trust policy includes the user's ARN
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
