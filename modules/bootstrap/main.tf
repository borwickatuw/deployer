# Bootstrap infrastructure
#
# Creates foundational resources that must exist before environment infrastructure:
# - S3 bucket for OpenTofu state storage (s3.tf)
# - IAM permissions boundary for ECS task roles (iam-boundary.tf)
# - IAM roles and policies for deployer operations (iam-*.tf)
#
# Usage:
#   This module should be instantiated per AWS account (e.g., bootstrap-staging/).
#   See bootstrap-staging/ for an example instance.
#
# IMPORTANT: When adding a new project, add its name to project_prefixes in
# the instance's terraform.tfvars and re-run tofu apply.

terraform {
  required_version = ">= 1.6.0"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "deployer"
      ManagedBy = "opentofu"
      Purpose   = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# DynamoDB for state locking is optional (requires additional IAM permissions)
# Uncomment if you have dynamodb:* permissions on the deployer-infra role
#
# resource "aws_dynamodb_table" "terraform_locks" {
#   name         = "deployer-terraform-locks"
#   billing_mode = "PAY_PER_REQUEST"
#   hash_key     = "LockID"
#   attribute {
#     name = "LockID"
#     type = "S"
#   }
#   lifecycle { prevent_destroy = true }
# }
#
# output "lock_table_name" {
#   value = aws_dynamodb_table.terraform_locks.name
# }
