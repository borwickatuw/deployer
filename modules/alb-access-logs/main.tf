# ALB Access Logs S3 Bucket
#
# Creates an S3 bucket with the required bucket policy for ALB access logging.
# ALB access logging requires a specific bucket policy that allows the regional
# ELB service account to write log files.

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "aws_region" {
  description = "AWS region (determines ELB service account ID)"
  type        = string
}

# ELB service account IDs per region
# https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html
locals {
  elb_account_ids = {
    "us-east-1"      = "127311923021"
    "us-east-2"      = "033677994240"
    "us-west-1"      = "027434742980"
    "us-west-2"      = "797873946194"
    "af-south-1"     = "098369216593"
    "ap-east-1"      = "754344448648"
    "ap-south-1"     = "718504428378"
    "ap-northeast-1" = "582318560864"
    "ap-northeast-2" = "600734575887"
    "ap-northeast-3" = "383597477331"
    "ap-southeast-1" = "114774131450"
    "ap-southeast-2" = "783225319266"
    "ca-central-1"   = "985666609251"
    "eu-central-1"   = "054676820928"
    "eu-west-1"      = "156460612806"
    "eu-west-2"      = "652711504416"
    "eu-west-3"      = "009996457667"
    "eu-north-1"     = "897822967062"
    "eu-south-1"     = "635631232127"
    "me-south-1"     = "076674570225"
    "sa-east-1"      = "507241528517"
  }
}

resource "aws_s3_bucket" "alb_logs" {
  bucket = "${var.name_prefix}-alb-logs"

  tags = {
    Name = "${var.name_prefix}-alb-logs"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-old-logs"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    expiration {
      days = 365
    }
  }
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.elb_account_ids[var.aws_region]}:root" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.alb_logs.arn}/*"
      }
    ]
  })
}

output "bucket_name" {
  description = "Name of the ALB access logs S3 bucket"
  value       = aws_s3_bucket.alb_logs.id
}

output "bucket_arn" {
  description = "ARN of the ALB access logs S3 bucket"
  value       = aws_s3_bucket.alb_logs.arn
}
