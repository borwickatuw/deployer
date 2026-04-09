# CloudFront ALB

Creates a CloudFront distribution in front of an ALB to provide custom error pages (502/503/504) when the backend is unavailable. No caching for dynamic content; forwards all headers/cookies for Cognito auth compatibility.

## Usage

```hcl
module "cloudfront" {
  source = "../../modules/cloudfront-alb"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1  # Required for CloudFront certificate
  }

  name_prefix     = "myapp-production"
  alb_dns_name    = module.alb.dns_name
  domain_name     = "myapp.example.com"
  route53_zone_id = aws_route53_zone.main.zone_id
}
```

## Key Variables

| Variable           | Type   | Description                                      |
| ------------------ | ------ | ------------------------------------------------ |
| name_prefix        | string | Prefix for resource names                        |
| alb_dns_name       | string | DNS name of the ALB origin                       |
| domain_name        | string | Domain name for the distribution                 |
| route53_zone_id    | string | Route 53 zone ID for certificate validation      |
| error_page_content | string | Custom HTML for 503 error page                   |
| price_class        | string | CloudFront price class (default: PriceClass_100) |

Requires an `aws.us_east_1` provider alias (CloudFront certificates must be in us-east-1).

## Outputs

| Output                      | Description                        |
| --------------------------- | ---------------------------------- |
| distribution_id             | CloudFront distribution ID         |
| distribution_arn            | CloudFront distribution ARN        |
| distribution_domain_name    | CloudFront domain name             |
| distribution_hosted_zone_id | Zone ID for Route 53 alias records |
| error_bucket_name           | S3 bucket name for error pages     |
| certificate_arn             | ACM certificate ARN (us-east-1)    |
