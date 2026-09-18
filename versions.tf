terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.x floor: modules/*/s3 declare `blocked_encryption_types` on
      # aws_s3_bucket_server_side_encryption_configuration, an argument the
      # 5.x provider rejects. Verify with `tofu validate` after `tofu init`.
      version = "~> 6.0"
    }
  }
}
