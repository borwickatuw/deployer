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
  environment     = "production"
  alb_dns_name    = module.alb.dns_name
  domain_name     = "myapp.example.com"
  route53_zone_id = aws_route53_zone.main.zone_id
}
```

## Key Variables

| Variable                 | Type   | Description                                                  |
| ------------------------ | ------ | ------------------------------------------------------------ |
| name_prefix              | string | Prefix for resource names                                    |
| environment              | string | `staging` or `production`; picks the default error page copy |
| alb_dns_name             | string | DNS name of the ALB origin                                   |
| domain_name              | string | Domain name for the distribution                             |
| route53_zone_id          | string | Route 53 zone ID for certificate validation                  |
| error_page_content       | string | Custom HTML for 503 error page                               |
| price_class              | string | CloudFront price class (default: PriceClass_100)             |
| viewer_ip_header_enabled | bool   | Attach the viewer-IP function (default: false)               |
| viewer_ip_header         | string | Header the function writes (default: `x-viewer-ip`)          |

Requires an `aws.us_east_1` provider alias (CloudFront certificates must be in us-east-1).

## Viewer IP Header

With `viewer_ip_header_enabled = true` the module creates a CloudFront Function
(`cloudfront-js-2.0`) and attaches it as a viewer-request function on the
default behavior, the one that reaches the ALB. The function sets
`viewer_ip_header` to the viewer address CloudFront observed, and replaces any
value the client sent. The `AllViewer` origin request policy forwards the header
to the ALB, where the [WAF](waf.md#behind-cloudfront) rate rule counts on it.
The error-page behavior goes to S3 and does not get the function. CloudFront
Functions are billed per invocation, so this adds a small per-request charge.

The root module turns this on only with the WAF rate rule enabled and
`alb_restrict_ingress_to_cloudfront` set. Without the ingress restriction,
anyone could send the header straight to the ALB.

## Outputs

| Output                      | Description                        |
| --------------------------- | ---------------------------------- |
| distribution_id             | CloudFront distribution ID         |
| distribution_arn            | CloudFront distribution ARN        |
| distribution_domain_name    | CloudFront domain name             |
| distribution_hosted_zone_id | Zone ID for Route 53 alias records |
| error_bucket_name           | S3 bucket name for error pages     |
| certificate_arn             | ACM certificate ARN (us-east-1)    |
