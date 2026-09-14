+++
title = "Monitoring Enhancements: Synthetic Monitoring"
+++

**Enhancement**: CloudWatch Synthetics canaries:

```hcl
resource "aws_synthetics_canary" "health_check" {
  name                 = "${var.name_prefix}-health"
  artifact_s3_location = "s3://${var.artifacts_bucket}/canary/"
  execution_role_arn   = aws_iam_role.canary.arn
  handler              = "health_check.handler"
  runtime_version      = "syn-nodejs-puppeteer-6.2"

  schedule {
    expression = "rate(5 minutes)"
  }

  # Canary script checks:
  # - Homepage loads
  # - Login works
  # - Key API endpoints respond
}
```

**Complexity**: Medium - requires canary script development.
