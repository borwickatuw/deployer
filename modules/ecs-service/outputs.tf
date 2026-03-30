# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

output "service_name" {
  value = aws_ecs_service.main.name
}

output "service_arn" {
  value = aws_ecs_service.main.id
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.main.arn
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}
