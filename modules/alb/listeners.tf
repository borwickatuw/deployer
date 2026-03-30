# ------------------------------------------------------------------------------
# HTTP Listener
# ------------------------------------------------------------------------------

# When HTTPS is enabled: redirect HTTP to HTTPS
# When HTTPS is disabled: forward directly to target group
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = local.https_enabled ? "redirect" : "forward"

    # Redirect config (when HTTPS enabled)
    dynamic "redirect" {
      for_each = local.https_enabled ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }

    # Forward config (when HTTPS disabled)
    target_group_arn = local.https_enabled ? null : aws_lb_target_group.default.arn
  }
}

# ------------------------------------------------------------------------------
# HTTPS Listener (when certificate provided)
# ------------------------------------------------------------------------------

# HTTPS listener WITHOUT authentication
resource "aws_lb_listener" "https" {
  count = local.https_enabled && !local.auth_enabled ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.default.arn
  }
}

# HTTPS listener WITH Cognito authentication
resource "aws_lb_listener" "https_with_auth" {
  count = local.https_enabled && local.auth_enabled ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  # Action 1: Authenticate with Cognito
  default_action {
    type  = "authenticate-cognito"
    order = 1

    authenticate_cognito {
      user_pool_arn       = var.cognito_auth.user_pool_arn
      user_pool_client_id = var.cognito_auth.user_pool_client_id
      user_pool_domain    = var.cognito_auth.user_pool_domain
    }
  }

  # Action 2: Forward to target group
  default_action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.default.arn
  }
}

# Health check rule - bypasses Cognito authentication
# This allows ALB health checks to reach the app without authentication
resource "aws_lb_listener_rule" "health_check" {
  count = local.https_enabled && local.auth_enabled ? 1 : 0

  listener_arn = aws_lb_listener.https_with_auth[0].arn
  priority     = 1 # Highest priority, evaluated before default action

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.default.arn
  }

  condition {
    path_pattern {
      values = ["/health", "/health/"]
    }
  }
}

# ------------------------------------------------------------------------------
# Path-Based Routing Rules (for additional target groups)
# ------------------------------------------------------------------------------

resource "aws_lb_listener_rule" "service_route" {
  for_each = { for k, v in var.additional_target_groups : k => v if local.https_enabled }

  listener_arn = local.active_https_listener_arn
  priority     = each.value.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service[each.key].arn
  }

  condition {
    path_pattern {
      values = [each.value.path_pattern]
    }
  }
}
