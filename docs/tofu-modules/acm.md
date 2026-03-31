# ACM

Creates an ACM SSL/TLS certificate with automatic DNS validation via Route 53.

## Usage

```hcl
module "acm" {
  source = "../../modules/acm"

  domain_name    = "myapp-staging.example.com"
  route53_zone_id = aws_route53_zone.main.zone_id
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| domain_name | string | Domain name for the certificate |
| route53_zone_id | string | Route 53 hosted zone ID for DNS validation |

## Outputs

| Output | Description |
| --- | --- |
| certificate_arn | ARN of the validated certificate |
| domain_name | Domain name of the certificate |
