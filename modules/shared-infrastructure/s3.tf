# S3 Buckets for Application Media Storage
#
# Two buckets per application:
# - Originals: Preservation copies of uploaded files (write-once, rare reads)
# - Media: Derivatives and thumbnails (write-once, frequent reads via CloudFront)
#
# Optional - only created when s3_storage_enabled = true

# ------------------------------------------------------------------------------
# S3 Originals Bucket
# ------------------------------------------------------------------------------

resource "aws_s3_bucket" "originals" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = "${var.name_prefix}-originals"

  tags = {
    Name        = "${var.name_prefix}-originals"
    Purpose     = "Original media files (preservation)"
    Environment = var.name_prefix
  }
}

resource "aws_s3_bucket_versioning" "originals" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = aws_s3_bucket.originals[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "originals" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = aws_s3_bucket.originals[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "originals" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = aws_s3_bucket.originals[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ------------------------------------------------------------------------------
# S3 Media Bucket
# ------------------------------------------------------------------------------

resource "aws_s3_bucket" "media" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = "${var.name_prefix}-media"

  tags = {
    Name        = "${var.name_prefix}-media"
    Purpose     = "Media derivatives (served via CloudFront)"
    Environment = var.name_prefix
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = aws_s3_bucket.media[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "media" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = aws_s3_bucket.media[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CORS configuration for media bucket (allows cross-origin viewers to load images)
resource "aws_s3_bucket_cors_configuration" "media" {
  count  = var.s3_storage_enabled ? 1 : 0
  bucket = aws_s3_bucket.media[0].id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag", "Content-Length", "Content-Type"]
    max_age_seconds = 86400
  }
}

