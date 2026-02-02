# CloudFront Distribution for Media Delivery
#
# Serves media files from S3 with:
# - Signed URL support for access control
# - Caching for performance
# - HTTPS enforcement
#
# Optional - only created when cloudfront_enabled = true

# ------------------------------------------------------------------------------
# CloudFront Origin Access Control
# ------------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "media" {
  count = var.cloudfront_enabled ? 1 : 0

  name                              = "${var.name_prefix}-media-oac"
  description                       = "OAC for ${var.name_prefix} media bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ------------------------------------------------------------------------------
# CloudFront Key Group (for signed URLs)
# ------------------------------------------------------------------------------

# Public key for signed URL validation
resource "aws_cloudfront_public_key" "media" {
  count = var.cloudfront_enabled && var.cloudfront_public_key_pem != "" ? 1 : 0

  name        = "${var.name_prefix}-media-key"
  encoded_key = var.cloudfront_public_key_pem
  comment     = "Public key for ${var.name_prefix} media signed URLs"
}

resource "aws_cloudfront_key_group" "media" {
  count = var.cloudfront_enabled && var.cloudfront_public_key_pem != "" ? 1 : 0

  name    = "${var.name_prefix}-media-key-group"
  items   = [aws_cloudfront_public_key.media[0].id]
  comment = "Key group for ${var.name_prefix} media signed URLs"
}

# ------------------------------------------------------------------------------
# CloudFront Distribution
# ------------------------------------------------------------------------------

resource "aws_cloudfront_distribution" "media" {
  count = var.cloudfront_enabled ? 1 : 0

  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name_prefix} media distribution"
  default_root_object = ""
  price_class         = var.cloudfront_price_class

  # Custom domain (optional)
  aliases = var.cloudfront_domain != "" ? [var.cloudfront_domain] : []

  # S3 origin
  origin {
    domain_name              = aws_s3_bucket.media[0].bucket_regional_domain_name
    origin_id                = "S3-${aws_s3_bucket.media[0].id}"
    origin_access_control_id = aws_cloudfront_origin_access_control.media[0].id
  }

  # Default cache behavior (for all media files)
  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-${aws_s3_bucket.media[0].id}"
    viewer_protocol_policy = "redirect-to-https"

    # Require signed URLs if key group is configured
    trusted_key_groups = var.cloudfront_public_key_pem != "" ? [aws_cloudfront_key_group.media[0].id] : []

    # Cache settings
    min_ttl     = 0
    default_ttl = 86400    # 1 day
    max_ttl     = 31536000 # 1 year

    # Forward headers for CORS
    forwarding_config {
      query_string = true
      cookies {
        forward = "none"
      }
      headers = ["Origin", "Access-Control-Request-Method", "Access-Control-Request-Headers"]
    }

    # Compress responses
    compress = true

    # Use managed cache policy
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_optimized.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.cors_s3_origin.id
  }

  # HLS streaming cache behavior (shorter TTL for playlists)
  ordered_cache_behavior {
    path_pattern           = "*.m3u8"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-${aws_s3_bucket.media[0].id}"
    viewer_protocol_policy = "redirect-to-https"

    trusted_key_groups = var.cloudfront_public_key_pem != "" ? [aws_cloudfront_key_group.media[0].id] : []

    min_ttl     = 0
    default_ttl = 3600  # 1 hour for playlists
    max_ttl     = 86400 # 1 day

    compress = true

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_optimized.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.cors_s3_origin.id
  }

  # Restrictions
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # SSL Certificate
  viewer_certificate {
    # Use custom certificate if domain provided, otherwise CloudFront default
    acm_certificate_arn            = var.cloudfront_domain != "" ? var.cloudfront_certificate_arn : null
    cloudfront_default_certificate = var.cloudfront_domain == ""
    minimum_protocol_version       = "TLSv1.2_2021"
    ssl_support_method             = var.cloudfront_domain != "" ? "sni-only" : null
  }

  tags = {
    Name        = "${var.name_prefix}-media-cdn"
    Environment = var.name_prefix
  }
}

# Managed cache policies
data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "cors_s3_origin" {
  name = "Managed-CORS-S3Origin"
}

# ------------------------------------------------------------------------------
# Route53 DNS for CloudFront (optional)
# ------------------------------------------------------------------------------

resource "aws_route53_record" "cloudfront" {
  count = var.cloudfront_enabled && var.cloudfront_domain != "" && var.route53_zone_id != null ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.cloudfront_domain
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.media[0].domain_name
    zone_id                = aws_cloudfront_distribution.media[0].hosted_zone_id
    evaluate_target_health = false
  }
}
