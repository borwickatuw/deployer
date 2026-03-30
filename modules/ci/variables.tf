# ------------------------------------------------------------------------------
# CI Module Variables (Shared Infrastructure)
# ------------------------------------------------------------------------------

variable "create_oidc_provider" {
  type        = bool
  description = "Create the GitHub OIDC provider. Set to false if it already exists (managed by another project)."
  default     = false
}
