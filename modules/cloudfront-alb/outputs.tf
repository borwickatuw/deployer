# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

output "distribution_id" {
  description = "CloudFront distribution ID"
  value       = aws_cloudfront_distribution.main.id
}

output "distribution_arn" {
  description = "CloudFront distribution ARN"
  value       = aws_cloudfront_distribution.main.arn
}

output "distribution_domain_name" {
  description = "CloudFront distribution domain name"
  value       = aws_cloudfront_distribution.main.domain_name
}

output "distribution_hosted_zone_id" {
  description = "CloudFront Route 53 zone ID (for alias records)"
  value       = aws_cloudfront_distribution.main.hosted_zone_id
}

output "error_bucket_name" {
  description = "Name of the S3 bucket for error pages"
  value       = aws_s3_bucket.error_pages.id
}

output "error_bucket_arn" {
  description = "ARN of the S3 bucket for error pages"
  value       = aws_s3_bucket.error_pages.arn
}

output "certificate_arn" {
  description = "ARN of the CloudFront ACM certificate (us-east-1)"
  value       = aws_acm_certificate.cloudfront.arn
}
