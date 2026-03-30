# ECS Service Module
#
# Creates an ECS Fargate service with task definition, IAM roles,
# and optional ALB integration.

data "aws_region" "current" {}

# ------------------------------------------------------------------------------
# CloudWatch Log Group
# ------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "main" {
  name              = var.log_group_name
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${var.name_prefix}-${var.service_name}-logs"
  }
}

# ------------------------------------------------------------------------------
# Task Definition
# ------------------------------------------------------------------------------

resource "aws_ecs_task_definition" "main" {
  family                   = "${var.name_prefix}-${var.service_name}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = var.service_name
      image     = var.image
      cpu       = var.cpu
      memory    = var.memory
      essential = true
      command   = var.command

      portMappings = local.has_port ? [
        {
          containerPort = var.container_port
          hostPort      = var.container_port
          protocol      = "tcp"
        }
      ] : []

      environment = [
        for k, v in var.environment_variables : {
          name  = k
          value = v
        }
      ]

      secrets = [
        for k, v in var.secrets : {
          name      = k
          valueFrom = v
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.main.name
          "awslogs-region"        = data.aws_region.current.id
          "awslogs-stream-prefix" = var.service_name
        }
      }
    }
  ])

  tags = {
    Name = "${var.name_prefix}-${var.service_name}"
  }
}

# ------------------------------------------------------------------------------
# ECS Service
# ------------------------------------------------------------------------------

resource "aws_ecs_service" "main" {
  name            = var.service_name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.main.arn
  desired_count   = var.desired_count

  # Standard Fargate (non-spot)
  dynamic "capacity_provider_strategy" {
    for_each = var.use_spot ? [] : [1]
    content {
      capacity_provider = "FARGATE"
      weight            = 1
    }
  }

  # Fargate Spot: guarantee 1 on-demand task, rest use spot
  dynamic "capacity_provider_strategy" {
    for_each = var.use_spot ? [1] : []
    content {
      capacity_provider = "FARGATE"
      base              = 1
      weight            = 0
    }
  }

  dynamic "capacity_provider_strategy" {
    for_each = var.use_spot ? [1] : []
    content {
      capacity_provider = "FARGATE_SPOT"
      weight            = 1
    }
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = var.alb_target_group_arn != null ? [1] : []
    content {
      target_group_arn = var.alb_target_group_arn
      container_name   = var.service_name
      container_port   = var.container_port
    }
  }

  # Service discovery registration (AWS Cloud Map)
  # Registers the service with a private DNS namespace for internal communication
  dynamic "service_registries" {
    for_each = var.service_discovery_registry_arn != null ? [1] : []
    content {
      registry_arn   = var.service_discovery_registry_arn
      container_name = var.service_name
      container_port = var.container_port
    }
  }

  lifecycle {
    ignore_changes = [desired_count] # Allow manual scaling
  }

  tags = {
    Name = "${var.name_prefix}-${var.service_name}"
  }
}
