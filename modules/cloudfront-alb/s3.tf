# ------------------------------------------------------------------------------
# S3 Bucket for Error Pages
# ------------------------------------------------------------------------------

resource "aws_s3_bucket" "error_pages" {
  bucket_prefix = "${var.name_prefix}-errors-"

  tags = {
    Name = "${var.name_prefix}-error-pages"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "error_pages" {
  bucket = aws_s3_bucket.error_pages.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "error_pages" {
  bucket = aws_s3_bucket.error_pages.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "error_pages" {
  bucket = aws_s3_bucket.error_pages.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_object" "error_503" {
  bucket       = aws_s3_bucket.error_pages.id
  key          = "error-503.html"
  content      = local.error_page_html
  content_type = "text/html"
  etag         = md5(local.error_page_html)
}

# ------------------------------------------------------------------------------
# Origin Access Control for S3
# ------------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "error_pages" {
  name                              = "${var.name_prefix}-errors-oac"
  description                       = "OAC for ${var.name_prefix} error pages bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# S3 bucket policy to allow CloudFront access
resource "aws_s3_bucket_policy" "error_pages" {
  bucket = aws_s3_bucket.error_pages.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontServicePrincipal"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.error_pages.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.main.arn
          }
        }
      }
    ]
  })
}
