# AWS Compute Optimizer
#
# Enables AWS Compute Optimizer at the account level for automated
# right-sizing recommendations. Once enabled, Compute Optimizer will:
#
# - Analyze ECS task CPU and memory utilization
# - Provide right-sizing recommendations after 14+ days of data
# - Quantify potential cost savings
#
# View recommendations in AWS Console -> Compute Optimizer -> ECS services
# Or via CLI: aws compute-optimizer get-ecs-service-recommendations

variable "include_member_accounts" {
  type        = bool
  default     = false
  description = "Include member accounts (requires AWS Organizations)"
}

resource "aws_computeoptimizer_enrollment_status" "main" {
  status = "Active"

  include_member_accounts = var.include_member_accounts
}

# Outputs

output "enrollment_status" {
  value       = aws_computeoptimizer_enrollment_status.main.status
  description = "Current Compute Optimizer enrollment status"
}
