# ------------------------------------------------------------------------------
# ALB Target Group (per-app)
# ------------------------------------------------------------------------------

resource "aws_lb_target_group" "app" {
  name        = substr(local.name_prefix, 0, 32) # ALB TG names max 32 chars
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = local.shared.vpc_id
  target_type = "ip"

  deregistration_delay = var.deregistration_delay

  health_check {
    path                = var.health_check_path
    healthy_threshold   = var.healthy_threshold
    unhealthy_threshold = var.unhealthy_threshold
    timeout             = var.health_check_timeout
    interval            = var.health_check_interval
    matcher             = "200-399"
  }

  tags = {
    Name = "${local.name_prefix}-tg"
  }
}

# ------------------------------------------------------------------------------
# ALB Listener Rule (per-app, host-based routing)
# ------------------------------------------------------------------------------

# Main listener rule for the app
resource "aws_lb_listener_rule" "app" {
  listener_arn = local.shared.alb_https_listener_arn != null ? local.shared.alb_https_listener_arn : local.shared.alb_http_listener_arn
  priority     = var.listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    host_header {
      values = [var.domain_name]
    }
  }

  tags = {
    Name = "${local.name_prefix}-rule"
  }
}

# Health check bypass rule (if Cognito auth is enabled on the shared ALB)
# This allows health checks to pass without authentication
resource "aws_lb_listener_rule" "health_check" {
  count = local.shared.cognito_auth_enabled ? 1 : 0

  listener_arn = local.shared.alb_https_listener_arn
  priority     = var.listener_rule_priority - 1 # Higher priority than main rule

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    host_header {
      values = [var.domain_name]
    }
  }

  condition {
    path_pattern {
      values = ["/health", "/health/"]
    }
  }

  tags = {
    Name = "${local.name_prefix}-health-rule"
  }
}
