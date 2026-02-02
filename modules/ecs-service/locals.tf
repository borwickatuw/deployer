# ------------------------------------------------------------------------------
# Locals
# ------------------------------------------------------------------------------

locals {
  has_port       = var.container_port != null
  has_s3_buckets = var.s3_originals_bucket_arn != "" || var.s3_media_bucket_arn != ""
}
