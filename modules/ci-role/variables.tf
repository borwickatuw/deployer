# ------------------------------------------------------------------------------
# CI Role Module Variables
# ------------------------------------------------------------------------------

variable "project_prefix" {
  description = "Project prefix for IAM resource scoping (e.g., 'myapp')"
  type        = string
}

variable "github_repo" {
  description = "GitHub org/repo that can assume this role (e.g., 'myorg/myapp')"
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC identity provider (from modules/ci output)"
  type        = string
}

variable "resolved_configs_bucket_arn" {
  description = "ARN of the resolved configs S3 bucket (from modules/ci output)"
  type        = string
}

variable "region" {
  description = "AWS region for resource ARNs"
  type        = string
}

variable "permissions_boundary" {
  description = "ARN of the IAM permissions boundary to attach to the role (required by infra-admin policy)"
  type        = string
}

variable "github_oidc_environments" {
  description = "GitHub environments to allow in OIDC trust policy"
  type        = list(string)
  default     = ["staging", "production"]
}
