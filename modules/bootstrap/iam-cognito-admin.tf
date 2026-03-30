# ------------------------------------------------------------------------------
# deployer-cognito-admin Role
# Used by: manage-cognito-access.py
# ------------------------------------------------------------------------------

data "aws_iam_policy_document" "cognito_admin" {
  count = var.create_iam_roles ? 1 : 0

  # Cognito user management
  statement {
    sid    = "CognitoUserManagement"
    effect = "Allow"
    actions = [
      "cognito-idp:DescribeUserPool",
      "cognito-idp:ListUsers",
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminDeleteUser",
      "cognito-idp:AdminDisableUser",
      "cognito-idp:AdminEnableUser",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminGetUser"
    ]
    resources = ["arn:aws:cognito-idp:${var.region}:${data.aws_caller_identity.current.account_id}:userpool/*"]
  }

  # SSM - Write test passwords
  statement {
    sid       = "SSMWriteTestPasswords"
    effect    = "Allow"
    actions   = ["ssm:PutParameter", "ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/deployer/*/cognito-test-password"]
  }

  # STS - Get caller identity
  statement {
    sid       = "TofuOutputRead"
    effect    = "Allow"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "cognito_admin" {
  count = var.create_iam_roles ? 1 : 0

  name               = "deployer-cognito-admin"
  assume_role_policy = data.aws_iam_policy_document.trust_policy[0].json
}

resource "aws_iam_role_policy" "cognito_admin" {
  count = var.create_iam_roles ? 1 : 0

  name   = "permissions"
  role   = aws_iam_role.cognito_admin[0].id
  policy = data.aws_iam_policy_document.cognito_admin[0].json
}
