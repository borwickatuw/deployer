# Cost Budget

Creates an AWS Budget that sends email alerts at 80% (forecasted) and 100% (actual) of a monthly cost threshold.

## Usage

```hcl
module "cost_budget" {
  source = "../../modules/cost-budget"

  name_prefix        = "myapp-production"
  budget_amount      = 100
  notification_email = "ops@example.com"
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| budget_amount | number | Monthly budget threshold in USD |
| notification_email | string | Email for budget alerts |
