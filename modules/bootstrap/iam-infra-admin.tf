# ------------------------------------------------------------------------------
# deployer-infra-admin Role
# Used by: OpenTofu (tofu.sh)
# Split into 4 managed policies due to AWS size limits
# ------------------------------------------------------------------------------

# Compute policy: VPC, ECS, ELB, Lambda, EventBridge
data "aws_iam_policy_document" "infra_admin_compute" {
  count = var.create_iam_roles ? 1 : 0

  # VPC and Networking
  statement {
    sid    = "VPCAndNetworking"
    effect = "Allow"
    actions = [
      "ec2:*Vpc*",
      "ec2:*Subnet*",
      "ec2:*InternetGateway*",
      "ec2:*NatGateway*",
      "ec2:*Address*",
      "ec2:*RouteTable*",
      "ec2:*Route",
      "ec2:*SecurityGroup*",
      "ec2:*NetworkInterface*",
      "ec2:*Tags*",
      "ec2:*FlowLog*",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeAccountAttributes"
    ]
    resources = ["*"]
  }

  # ECS
  statement {
    sid       = "ECS"
    effect    = "Allow"
    actions   = ["ecs:*"]
    resources = ["*"]
  }

  # Service Discovery (Cloud Map) - for ECS service-to-service communication
  statement {
    sid       = "ServiceDiscovery"
    effect    = "Allow"
    actions   = ["servicediscovery:*"]
    resources = ["*"]
  }

  # ELB
  statement {
    sid       = "ELB"
    effect    = "Allow"
    actions   = ["elasticloadbalancing:*"]
    resources = ["*"]
  }

  # Lambda (scoped to projects)
  statement {
    sid     = "Lambda"
    effect  = "Allow"
    actions = ["lambda:*"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${prefix}-*"
      ]
    ])
  }

  # EventBridge (scoped to projects)
  statement {
    sid     = "EventBridge"
    effect  = "Allow"
    actions = ["events:*"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/${prefix}-*"
      ]
    ])
  }

  # STS - Get caller identity
  statement {
    sid       = "STSIdentity"
    effect    = "Allow"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

# Data policy: RDS, ElastiCache, S3, ACM, CloudFront, Cognito, Route53, Logs, SSM, ECR, SecretsManager
data "aws_iam_policy_document" "infra_admin_data" {
  count = var.create_iam_roles ? 1 : 0

  # RDS
  statement {
    sid       = "RDS"
    effect    = "Allow"
    actions   = ["rds:*"]
    resources = ["*"]
  }

  # ElastiCache
  statement {
    sid       = "ElastiCache"
    effect    = "Allow"
    actions   = ["elasticache:*"]
    resources = ["*"]
  }

  # S3 (scoped to projects, terraform state, and resolved configs)
  statement {
    sid     = "S3"
    effect  = "Allow"
    actions = ["s3:*"]
    resources = concat(
      flatten([
        for prefix in var.project_prefixes : [
          "arn:aws:s3:::${prefix}-*",
          "arn:aws:s3:::${prefix}-*/*"
        ]
      ]),
      [
        "arn:aws:s3:::deployer-terraform-state-*",
        "arn:aws:s3:::deployer-terraform-state-*/*",
        "arn:aws:s3:::deployer-resolved-configs-*",
        "arn:aws:s3:::deployer-resolved-configs-*/*"
      ]
    )
  }

  # ACM
  statement {
    sid       = "ACM"
    effect    = "Allow"
    actions   = ["acm:*"]
    resources = ["*"]
  }

  # CloudFront
  statement {
    sid       = "CloudFront"
    effect    = "Allow"
    actions   = ["cloudfront:*"]
    resources = ["*"]
  }

  # Cognito
  statement {
    sid       = "Cognito"
    effect    = "Allow"
    actions   = ["cognito-idp:*"]
    resources = ["*"]
  }

  # Route53
  statement {
    sid       = "Route53"
    effect    = "Allow"
    actions   = ["route53:*"]
    resources = ["*"]
  }

  # CloudWatch Logs
  statement {
    sid       = "CloudWatchLogs"
    effect    = "Allow"
    actions   = ["logs:*"]
    resources = ["*"]
  }

  # SSM (scoped to projects and deployer)
  statement {
    sid     = "SSM"
    effect  = "Allow"
    actions = ["ssm:*"]
    resources = concat(
      flatten([
        for prefix in var.project_prefixes : [
          "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${prefix}/*"
        ]
      ]),
      [
        "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/deployer/*"
      ]
    )
  }

  # SSM - Describe parameters (global)
  statement {
    sid       = "SSMDescribe"
    effect    = "Allow"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }

  # ECR (scoped to projects)
  statement {
    sid     = "ECR"
    effect  = "Allow"
    actions = ["ecr:*"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${prefix}-*"
      ]
    ])
  }

  # ECR - Authorization (global)
  statement {
    sid       = "ECRAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # Secrets Manager (scoped to projects)
  statement {
    sid     = "SecretsManager"
    effect  = "Allow"
    actions = ["secretsmanager:*"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${prefix}-*"
      ]
    ])
  }
}

# IAM policy: Role management (kept granular due to sensitivity)
data "aws_iam_policy_document" "infra_admin_iam" {
  count = var.create_iam_roles ? 1 : 0

  # Create roles with boundary requirement
  statement {
    sid     = "CreateRolesWithBoundary"
    effect  = "Allow"
    actions = ["iam:CreateRole"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${prefix}-*"
      ]
    ])
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/deployer-ecs-role-boundary"]
    }
  }

  # Manage roles
  statement {
    sid    = "ManageRoles"
    effect = "Allow"
    actions = [
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePermissionsBoundary",
      "iam:DeleteRolePermissionsBoundary",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags"
    ]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${prefix}-*"
      ]
    ])
  }

  # Manage inline policies
  statement {
    sid    = "ManageInlinePolicies"
    effect = "Allow"
    actions = [
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy"
    ]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${prefix}-*"
      ]
    ])
  }

  # Attach managed policies (only specific AWS-managed policies)
  statement {
    sid    = "AttachManagedPolicies"
    effect = "Allow"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy"
    ]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${prefix}-*"
      ]
    ])
    condition {
      test     = "ArnLike"
      variable = "iam:PolicyARN"
      values = [
        "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
        "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole",
        "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
      ]
    }
  }

  # Pass roles to services
  statement {
    sid     = "PassRolesToServices"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${prefix}-*"
      ]
    ])
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values = [
        "ecs-tasks.amazonaws.com",
        "lambda.amazonaws.com",
        "events.amazonaws.com",
        "rds.amazonaws.com",
        "monitoring.rds.amazonaws.com",
        "vpc-flow-logs.amazonaws.com"
      ]
    }
  }

  # Create service-linked roles
  statement {
    sid       = "CreateServiceLinkedRoles"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values = [
        "ecs.amazonaws.com",
        "elasticloadbalancing.amazonaws.com",
        "rds.amazonaws.com",
        "elasticache.amazonaws.com"
      ]
    }
  }

  # Read policies
  statement {
    sid    = "ReadPolicies"
    effect = "Allow"
    actions = [
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions"
    ]
    resources = ["*"]
  }
}

# WAF policy
data "aws_iam_policy_document" "infra_admin_waf" {
  count = var.create_iam_roles ? 1 : 0

  # WAFv2
  statement {
    sid       = "WAFv2"
    effect    = "Allow"
    actions   = ["wafv2:*"]
    resources = ["*"]
  }

  # CloudWatch Logs for WAF
  statement {
    sid    = "CloudWatchLogsForWAF"
    effect = "Allow"
    actions = [
      "logs:PutResourcePolicy",
      "logs:DeleteResourcePolicy",
      "logs:DescribeResourcePolicies"
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "infra_admin" {
  count = var.create_iam_roles ? 1 : 0

  name               = "deployer-infra-admin"
  assume_role_policy = data.aws_iam_policy_document.trust_policy[0].json
}

resource "aws_iam_policy" "infra_admin_compute" {
  count = var.create_iam_roles ? 1 : 0

  name        = "deployer-infra-admin-compute"
  description = "OpenTofu: VPC, ECS, ELB, Lambda, EventBridge"
  policy      = data.aws_iam_policy_document.infra_admin_compute[0].json
}

resource "aws_iam_policy" "infra_admin_data" {
  count = var.create_iam_roles ? 1 : 0

  name        = "deployer-infra-admin-data"
  description = "OpenTofu: RDS, ElastiCache, S3, Cognito, Route53"
  policy      = data.aws_iam_policy_document.infra_admin_data[0].json
}

resource "aws_iam_policy" "infra_admin_iam" {
  count = var.create_iam_roles ? 1 : 0

  name        = "deployer-infra-admin-iam"
  description = "OpenTofu: IAM role management"
  policy      = data.aws_iam_policy_document.infra_admin_iam[0].json
}

resource "aws_iam_policy" "infra_admin_waf" {
  count = var.create_iam_roles ? 1 : 0

  name        = "deployer-infra-admin-waf"
  description = "OpenTofu: WAF Web ACL management"
  policy      = data.aws_iam_policy_document.infra_admin_waf[0].json
}

resource "aws_iam_role_policy_attachment" "infra_admin_compute" {
  count = var.create_iam_roles ? 1 : 0

  role       = aws_iam_role.infra_admin[0].name
  policy_arn = aws_iam_policy.infra_admin_compute[0].arn
}

resource "aws_iam_role_policy_attachment" "infra_admin_data" {
  count = var.create_iam_roles ? 1 : 0

  role       = aws_iam_role.infra_admin[0].name
  policy_arn = aws_iam_policy.infra_admin_data[0].arn
}

resource "aws_iam_role_policy_attachment" "infra_admin_iam" {
  count = var.create_iam_roles ? 1 : 0

  role       = aws_iam_role.infra_admin[0].name
  policy_arn = aws_iam_policy.infra_admin_iam[0].arn
}

resource "aws_iam_role_policy_attachment" "infra_admin_waf" {
  count = var.create_iam_roles ? 1 : 0

  role       = aws_iam_role.infra_admin[0].name
  policy_arn = aws_iam_policy.infra_admin_waf[0].arn
}
