# ------------------------------------------------------------------------------
# Bootstrap Variables
# ------------------------------------------------------------------------------

variable "region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-west-2"
}

variable "project_prefixes" {
  description = "Project name prefixes for resource patterns in IAM policies (e.g., ['myapp', 'otherapp'])"
  type        = list(string)
}

variable "trusted_user_arns" {
  description = "List of IAM user ARNs that can assume the deployer roles"
  type        = list(string)
}

variable "create_iam_roles" {
  description = "Whether to create IAM roles and policies. Set to false if roles already exist and only bootstrap resources are needed."
  type        = bool
  default     = true
}

variable "ecr_pull_repositories" {
  description = <<-EOT
    ECR repository names (unprefixed, in this account) the deploy role may
    pull from in addition to the project-prefixed ones. For a base image an
    application builds FROM that is published by another project into this
    account — havoc's blocks worker builds FROM blocker's `blocker`
    repository — the deploy role's Docker session pulls it during the build.
  EOT
  type        = list(string)
  default     = []
}
