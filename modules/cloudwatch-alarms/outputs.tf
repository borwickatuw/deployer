# CloudWatch Alarms Module - Outputs

output "sns_topic_arn" {
  description = "ARN of the SNS topic for alarm notifications"
  value       = aws_sns_topic.alarms.arn
}

output "sns_topic_name" {
  description = "Name of the SNS topic for alarm notifications"
  value       = aws_sns_topic.alarms.name
}

output "alb_alarm_arns" {
  description = "ARNs of ALB-related alarms"
  value = compact([
    try(aws_cloudwatch_metric_alarm.alb_5xx[0].arn, ""),
    try(aws_cloudwatch_metric_alarm.alb_latency[0].arn, ""),
    try(aws_cloudwatch_metric_alarm.alb_unhealthy_hosts[0].arn, ""),
  ])
}

output "rds_alarm_arns" {
  description = "ARNs of RDS-related alarms"
  value = compact([
    try(aws_cloudwatch_metric_alarm.rds_cpu[0].arn, ""),
    try(aws_cloudwatch_metric_alarm.rds_storage[0].arn, ""),
    try(aws_cloudwatch_metric_alarm.rds_connections[0].arn, ""),
  ])
}

output "elasticache_alarm_arns" {
  description = "ARNs of ElastiCache-related alarms"
  value = compact([
    try(aws_cloudwatch_metric_alarm.elasticache_memory[0].arn, ""),
    try(aws_cloudwatch_metric_alarm.elasticache_cpu[0].arn, ""),
  ])
}

output "ecs_alarm_arns" {
  description = "ARNs of ECS-related alarms"
  value       = [for alarm in aws_cloudwatch_metric_alarm.ecs_running_tasks : alarm.arn]
}

output "all_alarm_arns" {
  description = "ARNs of all created alarms"
  value = concat(
    compact([
      try(aws_cloudwatch_metric_alarm.alb_5xx[0].arn, ""),
      try(aws_cloudwatch_metric_alarm.alb_latency[0].arn, ""),
      try(aws_cloudwatch_metric_alarm.alb_unhealthy_hosts[0].arn, ""),
      try(aws_cloudwatch_metric_alarm.rds_cpu[0].arn, ""),
      try(aws_cloudwatch_metric_alarm.rds_storage[0].arn, ""),
      try(aws_cloudwatch_metric_alarm.rds_connections[0].arn, ""),
      try(aws_cloudwatch_metric_alarm.elasticache_memory[0].arn, ""),
      try(aws_cloudwatch_metric_alarm.elasticache_cpu[0].arn, ""),
    ]),
    [for alarm in aws_cloudwatch_metric_alarm.ecs_running_tasks : alarm.arn]
  )
}
