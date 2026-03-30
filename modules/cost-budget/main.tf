# Cost Budget Module
#
# Creates an AWS Budget that alerts when monthly costs approach or exceed
# a threshold. Sends email notifications at 80% and 100% of budget.
#
# Usage:
#   module "cost_budget" {
#     source = "../../modules/cost-budget"
#
#     name_prefix        = local.name_prefix
#     budget_amount      = 100
#     notification_email = "ops@example.com"
#   }

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly-cost"
  budget_type  = "COST"
  limit_amount = var.budget_amount
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
  }

  tags = {
    Name = "${var.name_prefix}-monthly-cost"
  }
}
