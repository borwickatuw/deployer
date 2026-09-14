+++
title = "Monitoring Enhancements: Dashboards as Code"
+++

**Current state**: Dashboards created manually in AWS Console.

**Enhancement**: CloudWatch dashboards managed in Terraform via `aws_cloudwatch_dashboard` resource. A standard production dashboard would include:

- ECS: CPU/Memory per service, running task count
- ALB: Request count, latency percentiles, error rates
- RDS: CPU, connections, IOPS, free storage
- ElastiCache: Memory, CPU, connections

**Example**:

```hcl
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name_prefix}-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", "web", "ClusterName", var.cluster_name]
          ]
          title = "ECS CPU Utilization"
        }
      }
      # ... more widgets
    ]
  })
}
```

**Why someday**: Dashboards are easy to create manually in the console and don't drift much once created. The Terraform JSON is verbose (100+ lines for a basic dashboard) for relatively little ongoing benefit.

**Complexity**: Medium - verbose but straightforward.
