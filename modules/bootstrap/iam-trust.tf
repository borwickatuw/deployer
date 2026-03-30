# ------------------------------------------------------------------------------
# Trust Policy (shared by all deployer IAM roles)
# ------------------------------------------------------------------------------

data "aws_iam_policy_document" "trust_policy" {
  count = var.create_iam_roles ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.trusted_user_arns
    }
  }
}
