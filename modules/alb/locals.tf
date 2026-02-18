# ------------------------------------------------------------------------------
# Locals
# ------------------------------------------------------------------------------

locals {
  https_enabled = var.certificate_arn != null
  auth_enabled  = var.cognito_auth != null

  # The active HTTPS listener ARN (with or without auth)
  active_https_listener_arn = local.https_enabled ? (
    local.auth_enabled ? aws_lb_listener.https_with_auth[0].arn : aws_lb_listener.https[0].arn
  ) : null
}
