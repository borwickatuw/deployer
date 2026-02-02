# Getting Started

This guide covers initial AWS account setup for the deployer. After completing this guide, you'll have a secure IAM configuration with least-privilege roles.

## Prerequisites

- AWS account with Administrator access (for initial setup only)
- AWS CLI configured (`aws configure`)
- OpenTofu installed (`brew install opentofu`)
- uv installed for Python (`brew install uv`)

## Initial Account Setup

These steps require Administrator access and only need to be done once.

### 1. Create Route 53 Hosted Zone

If you don't already have a Route 53 hosted zone for your domain:

```bash
aws route53 create-hosted-zone \
  --name yourdomain.com \
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

Create a bootstrap instance for your account. If you're starting from scratch, copy the example files:

```bash
cd ~/code/deployer-environments

# If starting fresh, copy from the deployer example
cp -r /path/to/deployer/example-deployer-environments/bootstrap bootstrap/
cp -r /path/to/deployer/example-deployer-environments/bootstrap-example bootstrap-myaccount/

# Rename .example files
cd bootstrap-myaccount
mv main.tf.example main.tf
mv terraform.tfvars.example terraform.tfvars
```

Edit `bootstrap-myaccount/terraform.tfvars`:

```hcl
region = "us-west-2"

# Add your project names
project_prefixes = ["myapp", "otherapp"]

# Update with your IAM user ARN
trusted_user_arns = ["arn:aws:iam::123456789012:user/deployer"]
```

### 4. Apply Bootstrap Infrastructure

Run the bootstrap to create IAM roles, policies, state bucket, and permissions boundary:

```bash
cd ~/code/deployer-environments/bootstrap-myaccount
AWS_PROFILE=admin tofu init
AWS_PROFILE=admin tofu plan
AWS_PROFILE=admin tofu apply
```

This creates:
- S3 bucket for terraform state
- ECS role permissions boundary
- `deployer-app-deploy` role (for deploy.py)
- `deployer-infra-admin` role (for OpenTofu)
- `deployer-cognito-admin` role (for Cognito management)

**Note:** If you have existing IAM resources from a previous setup, run the import script first:
```bash
AWS_PROFILE=admin ../bootstrap/import-existing.sh
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

## First Environment Deployment

### 1. Create Environment Directory

```bash
mkdir -p environments/myapp-staging
cd environments/myapp-staging
```

### 2. Create terraform.tfvars

```hcl
project_name    = "myapp"
environment     = "staging"
db_name         = "myapp"
db_username     = "myapp_admin"
db_password     = "generate-a-strong-password"
domain_name     = "staging.myapp.com"
route53_zone_id = "Z..."  # Your hosted zone ID

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
```

### 3. Create main.tf

```hcl
# Point to the root module
module "infrastructure" {
  source = "../.."
}
```

### 4. Initialize and Apply OpenTofu

```bash
# Use the tofu wrapper (auto-selects correct profile)
./bin/tofu.sh -chdir=environments/myapp-staging init
./bin/tofu.sh -chdir=environments/myapp-staging plan
./bin/tofu.sh -chdir=environments/myapp-staging apply
```

### 5. Create ECR Repository

```bash
AWS_PROFILE=deployer-infra aws ecr create-repository \
  --repository-name myapp-web \
  --region us-west-2
```

### 6. Deploy Your Application

```bash
# Profile is auto-selected from environment's config.toml
uv run python bin/deploy.py \
  /path/to/your-app/deploy.toml \
  myapp-staging
```

## Removing Administrator Access

Once you've verified all roles work correctly:

1. **Remove AdministratorAccess** from any existing IAM user
2. **Document** that admin access is only needed for:
   - Modifying the deployer IAM policies themselves
   - Adding new projects (requires updating ARN patterns)
   - Creating the initial IAM infrastructure

## Adding New Projects

When you need to add a new project (not just a new environment of an existing project):

1. **Update bootstrap configuration**:
   - Edit your bootstrap instance's `terraform.tfvars`
   - Add the project name to `project_prefixes`

2. **Apply the changes**:
   ```bash
   cd ~/code/deployer-environments/bootstrap-myaccount
   AWS_PROFILE=admin tofu plan
   AWS_PROFILE=admin tofu apply
   ```

   This updates:
   - IAM policies with the new project ARN patterns
   - ECS permissions boundary

3. **Create ECR repository** for the new project

## Multi-Account Setup

For staging and production in different AWS accounts, create separate bootstrap instances:

```
deployer-environments/
├── bootstrap/                    # Shared module
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

- [DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md) - Full deployment workflow
- [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) - Configuration options
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
