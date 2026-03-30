# CloudFront Distribution in Front of ALB
#
# Creates a CloudFront distribution that sits in front of an ALB to provide
# custom error pages (e.g., 503) when the backend is unavailable.
#
# Key features:
# - ACM certificate in us-east-1 (CloudFront requirement)
# - S3 bucket for error pages with OAC
# - No caching for dynamic/authenticated content (CachingDisabled policy)
# - Forwards all headers/cookies for Cognito auth (AllViewer policy)
# - Custom error responses for 502, 503, 504

terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws, aws.us_east_1]
    }
  }
}

# ------------------------------------------------------------------------------
# CloudFront Distribution
# ------------------------------------------------------------------------------

resource "aws_cloudfront_distribution" "main" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "CloudFront for ${var.name_prefix} with custom error pages"
  price_class     = var.price_class
  aliases         = [var.domain_name]

  # Primary origin: ALB
  origin {
    domain_name = var.alb_dns_name
    origin_id   = "ALB"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # Secondary origin: S3 error pages
  origin {
    domain_name              = aws_s3_bucket.error_pages.bucket_regional_domain_name
    origin_id                = "S3-ErrorPages"
    origin_access_control_id = aws_cloudfront_origin_access_control.error_pages.id
  }

  # Default cache behavior: forward everything to ALB with no caching
  default_cache_behavior {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "ALB"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    # Use managed policies for no caching and forwarding all headers
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3" # AllViewer
  }

  # Cache behavior for error pages - route to S3
  ordered_cache_behavior {
    path_pattern           = "/error-*.html"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-ErrorPages"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    # Cache error pages
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6" # CachingOptimized
  }

  # Custom error responses - serve S3 error page
  custom_error_response {
    error_code            = 502
    response_code         = 503
    response_page_path    = "/error-503.html"
    error_caching_min_ttl = var.error_caching_min_ttl
  }

  custom_error_response {
    error_code            = 503
    response_code         = 503
    response_page_path    = "/error-503.html"
    error_caching_min_ttl = var.error_caching_min_ttl
  }

  custom_error_response {
    error_code            = 504
    response_code         = 503
    response_page_path    = "/error-503.html"
    error_caching_min_ttl = var.error_caching_min_ttl
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.cloudfront.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = {
    Name = "${var.name_prefix}-cloudfront-alb"
  }

  depends_on = [aws_acm_certificate_validation.cloudfront]
}
