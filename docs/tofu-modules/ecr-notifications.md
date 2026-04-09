# ECR Notifications

Sends SNS notifications when ECR image scans find CRITICAL severity vulnerabilities. Uses EventBridge to monitor scan results.

## Usage

```hcl
module "ecr_notifications" {
  source = "../../modules/ecr-notifications"

  name_prefix   = "myapp-production"
  sns_topic_arn = module.alarms.sns_topic_arn
}
```

## Key Variables

| Variable      | Type   | Description                                   |
| ------------- | ------ | --------------------------------------------- |
| name_prefix   | string | Prefix for resource names                     |
| sns_topic_arn | string | SNS topic ARN for vulnerability notifications |

## Outputs

| Output         | Description                                |
| -------------- | ------------------------------------------ |
| event_rule_arn | EventBridge rule ARN for ECR scan findings |
