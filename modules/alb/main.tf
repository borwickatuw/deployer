# Application Load Balancer
#
# Creates an ALB with optional HTTPS and Cognito authentication support.
# - When certificate_arn is provided: HTTPS enabled, HTTP redirects to HTTPS
# - When cognito_auth is provided: requests require authentication before forwarding

# ------------------------------------------------------------------------------
# Load Balancer
# ------------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${var.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = var.deletion_protection
  drop_invalid_header_fields = true

  # Idle timeout - increase for large file uploads (default 60, max 4000)
  idle_timeout = var.idle_timeout

  dynamic "access_logs" {
    for_each = var.access_logs_enabled ? [1] : []
    content {
      bucket  = var.access_logs_bucket
      prefix  = var.access_logs_prefix
      enabled = true
    }
  }

  tags = {
    Name = "${var.name_prefix}-alb"
  }
}

# ------------------------------------------------------------------------------
# Default Target Group
# ------------------------------------------------------------------------------

resource "aws_lb_target_group" "default" {
  name        = "${var.name_prefix}-default"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  deregistration_delay = var.deregistration_delay

  health_check {
    path                = var.default_health_check_path
    healthy_threshold   = var.healthy_threshold
    unhealthy_threshold = var.unhealthy_threshold
    timeout             = var.health_check_timeout
    interval            = var.health_check_interval
    matcher             = "200-399"
  }
}

# ------------------------------------------------------------------------------
# Additional Target Groups (for path-based routing)
# ------------------------------------------------------------------------------

resource "aws_lb_target_group" "service" {
  for_each = var.additional_target_groups

  name        = "${var.name_prefix}-${each.key}"
  port        = each.value.port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  deregistration_delay = var.deregistration_delay

  health_check {
    path                = each.value.health_check_path
    healthy_threshold   = var.healthy_threshold
    unhealthy_threshold = var.unhealthy_threshold
    timeout             = var.health_check_timeout
    interval            = var.health_check_interval
    matcher             = coalesce(each.value.health_check_matcher, "200-399")
  }
}
