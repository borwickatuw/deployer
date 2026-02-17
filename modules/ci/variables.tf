# ------------------------------------------------------------------------------
# CI Module Variables
# ------------------------------------------------------------------------------

variable "region" {
  description = "AWS region for resources"
  type        = string
}

variable "github_ci_repos" {
  description = "Map of project prefix to GitHub org/repo for CI deploy roles"
  type        = map(string)
  default     = {}
  # Example: { myapp = "myorg/myapp", anotherapp = "myorg/anotherapp" }
}

variable "github_oidc_environments" {
  description = "GitHub environments to allow in OIDC trust policy"
  type        = list(string)
  default     = ["staging", "production"]
}
