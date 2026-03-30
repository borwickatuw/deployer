# CloudWatch Alarms Module
#
# Creates standard production alarms with SNS email notifications.
# Use this module to set up baseline monitoring for production environments.
#
# Example usage:
#
#   module "alarms" {
#     source = "../../modules/cloudwatch-alarms"
#
#     name_prefix        = "myapp-production"
#     notification_email = "ops@example.com"
#
#     alb_arn_suffix          = module.alb.arn_suffix
#     target_group_arn_suffix = module.alb.target_group_arn_suffix
#     rds_instance_id         = module.rds.db_instance_id
#     elasticache_cluster_id  = module.elasticache.cluster_id
#     ecs_cluster_name        = module.ecs_cluster.name
#     ecs_service_names       = ["web", "celery"]
#   }

locals {
  default_tags = {
    Module = "cloudwatch-alarms"
  }
  tags = merge(local.default_tags, var.tags)
}

# SNS Topic for alarm notifications
resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# =============================================================================
# ALB Alarms
# =============================================================================

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  count = var.enable_alb_alarms && var.alb_arn_suffix != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-alb-5xx-high"
  alarm_description   = "ALB 5XX error rate is high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alb_5xx_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_latency" {
  count = var.enable_alb_alarms && var.alb_arn_suffix != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-alb-latency-high"
  alarm_description   = "ALB target response time p95 is high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  extended_statistic  = "p95"
  threshold           = var.alb_latency_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_hosts" {
  count = var.enable_alb_alarms && var.target_group_arn_suffix != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-alb-unhealthy-hosts"
  alarm_description   = "ALB has unhealthy targets"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

# =============================================================================
# RDS Alarms
# =============================================================================

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  count = var.enable_rds_alarms && var.rds_instance_id != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-rds-cpu-high"
  alarm_description   = "RDS CPU utilization is high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = var.rds_cpu_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  count = var.enable_rds_alarms && var.rds_instance_id != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-rds-storage-low"
  alarm_description   = "RDS free storage is low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = var.rds_storage_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  count = var.enable_rds_alarms && var.rds_instance_id != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-rds-connections-high"
  alarm_description   = "RDS database connections are high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  # Note: This is a static threshold. For percentage-based, you'd need to know max_connections
  # which varies by instance size. Consider adjusting based on your instance class.
  # db.t3.micro: ~87, db.r6g.large: ~1700
  threshold          = 100
  treat_missing_data = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

# =============================================================================
# ElastiCache Alarms
# =============================================================================

resource "aws_cloudwatch_metric_alarm" "elasticache_memory" {
  count = var.enable_elasticache_alarms && var.elasticache_cluster_id != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-elasticache-memory-high"
  alarm_description   = "ElastiCache memory usage is high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = var.elasticache_memory_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    CacheClusterId = var.elasticache_cluster_id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "elasticache_cpu" {
  count = var.enable_elasticache_alarms && var.elasticache_cluster_id != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-elasticache-cpu-high"
  alarm_description   = "ElastiCache CPU utilization is high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "EngineCPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 90
  treat_missing_data  = "notBreaching"

  dimensions = {
    CacheClusterId = var.elasticache_cluster_id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

# =============================================================================
# ECS Alarms
# =============================================================================

resource "aws_cloudwatch_metric_alarm" "ecs_running_tasks" {
  for_each = var.enable_ecs_alarms && var.ecs_cluster_name != "" ? toset(var.ecs_service_names) : toset([])

  alarm_name          = "${var.name_prefix}-ecs-${each.key}-tasks-low"
  alarm_description   = "ECS service ${each.key} has fewer running tasks than desired"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "RunningTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = 60
  statistic           = "Average"
  # Threshold of 1 means alarm if running tasks drop below 1
  # For production, you typically want at least 1 task running
  threshold          = 1
  treat_missing_data = "breaching"

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = each.key
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}
