#!/bin/bash
#
# Import existing IAM resources into terraform state
#
# Run this script if IAM roles were previously created by iam-policies/setup.sh
# and you want to migrate them to terraform management.
#
# Prerequisites:
#   - AWS CLI configured with admin access
#   - terraform/tofu initialized (run `tofu init` first)
#
# Usage:
#   cd ~/code/deployer-environments/bootstrap-example
#   AWS_PROFILE=admin ./import-existing.sh

set -e

# Get AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account ID: $ACCOUNT_ID"
echo ""

# Check if we're in a bootstrap instance directory (has module "bootstrap" reference)
if ! grep -q 'module "bootstrap"' main.tf 2>/dev/null; then
    echo "Error: This script should be run from a bootstrap instance directory"
    echo "       (e.g., bootstrap-example/), not from the bootstrap module itself."
    exit 1
fi

echo "=== Importing IAM Roles ==="
echo ""

# Import roles
echo "Importing deployer-app-deploy role..."
tofu import 'module.bootstrap.aws_iam_role.app_deploy[0]' deployer-app-deploy || echo "  (may already exist or not found)"

echo "Importing deployer-infra-admin role..."
tofu import 'module.bootstrap.aws_iam_role.infra_admin[0]' deployer-infra-admin || echo "  (may already exist or not found)"

echo "Importing deployer-cognito-admin role..."
tofu import 'module.bootstrap.aws_iam_role.cognito_admin[0]' deployer-cognito-admin || echo "  (may already exist or not found)"

echo ""
echo "=== Importing Managed Policies ==="
echo ""

# Import managed policies (infra-admin uses managed policies due to size limits)
echo "Importing deployer-infra-admin-compute policy..."
tofu import "module.bootstrap.aws_iam_policy.infra_admin_compute[0]" "arn:aws:iam::${ACCOUNT_ID}:policy/deployer-infra-admin-compute" || echo "  (may already exist or not found)"

echo "Importing deployer-infra-admin-data policy..."
tofu import "module.bootstrap.aws_iam_policy.infra_admin_data[0]" "arn:aws:iam::${ACCOUNT_ID}:policy/deployer-infra-admin-data" || echo "  (may already exist or not found)"

echo "Importing deployer-infra-admin-iam policy..."
tofu import "module.bootstrap.aws_iam_policy.infra_admin_iam[0]" "arn:aws:iam::${ACCOUNT_ID}:policy/deployer-infra-admin-iam" || echo "  (may already exist or not found)"

echo "Importing deployer-infra-admin-waf policy..."
tofu import "module.bootstrap.aws_iam_policy.infra_admin_waf[0]" "arn:aws:iam::${ACCOUNT_ID}:policy/deployer-infra-admin-waf" || echo "  (may already exist or not found)"

echo ""
echo "=== Importing S3 Bucket ==="
echo ""

echo "Importing terraform state bucket..."
tofu import 'module.bootstrap.aws_s3_bucket.terraform_state' "deployer-terraform-state-${ACCOUNT_ID}" || echo "  (may already exist or not found)"

echo ""
echo "=== Importing ECS Permissions Boundary ==="
echo ""

echo "Importing deployer-ecs-role-boundary policy..."
tofu import "module.bootstrap.aws_iam_policy.ecs_role_boundary" "arn:aws:iam::${ACCOUNT_ID}:policy/deployer-ecs-role-boundary" || echo "  (may already exist or not found)"

echo ""
echo "=== Import Complete ==="
echo ""
echo "Next steps:"
echo "1. Run 'tofu plan' to see if any changes are needed"
echo "2. Review the plan carefully - some policy attachments may need to be re-applied"
echo "3. Run 'tofu apply' to synchronize state"
echo ""
echo "Note: Inline policies on roles (app_deploy, cognito_admin) and policy attachments"
echo "      may not be imported and will be created fresh by terraform."
