# ------------------------------------------------------------------------------
# User-side Assume-Role Policy
#
# Grants each trusted IAM user permission to call sts:AssumeRole on the
# three deployer roles. This is the "other side" of the trust policy in
# iam-trust.tf — AWS requires both the role to trust the caller AND the
# caller to have permission to assume the role.
# ------------------------------------------------------------------------------

locals {
  # Extract usernames from trusted_user_arns (skip non-user ARNs like roles)
  trusted_user_names = var.create_iam_roles ? [
    for arn in var.trusted_user_arns :
    regex(".*:user/(.+)$", arn)[0]
    if can(regex(".*:user/(.+)$", arn))
  ] : []
}

data "aws_iam_policy_document" "assume_deployer_roles" {
  count = var.create_iam_roles ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      aws_iam_role.app_deploy[0].arn,
      aws_iam_role.infra_admin[0].arn,
      aws_iam_role.cognito_admin[0].arn,
    ]
  }
}

resource "aws_iam_user_policy" "assume_deployer_roles" {
  for_each = var.create_iam_roles ? toset(local.trusted_user_names) : toset([])

  name   = "assume-deployer-roles"
  user   = each.value
  policy = data.aws_iam_policy_document.assume_deployer_roles[0].json
}
