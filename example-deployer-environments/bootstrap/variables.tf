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
