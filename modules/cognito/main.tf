# Cognito User Pool for ALB Authentication
#
# Creates a user pool for authenticating access to staging environments.
# Users are created manually by administrators (no self-signup).

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "domain_name" {
  description = "Domain name used for ALB (for OAuth callback URL)"
  type        = string
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}

# User Pool
resource "aws_cognito_user_pool" "main" {
  name = "${var.name_prefix}-users"

  # Only admins can create users - no self-signup
  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_subject = "${var.name_prefix} - Your staging access credentials"
      email_message = "You have been granted access to the ${var.name_prefix} staging environment.\n\nUsername: {username}\nTemporary password: {####}\n\nPlease log in and change your password."
      sms_message   = "Your ${var.name_prefix} staging credentials: {username} / {####}"
    }
  }

  # Password policy
  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = false
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  # Account recovery via email
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Auto-verify email when admin creates user
  auto_verified_attributes = ["email"]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-cognito"
  })
}

# Cognito Domain (uses AWS-hosted domain)
# Creates: <name_prefix>.auth.<region>.amazoncognito.com
resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.name_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}

# User Pool Client for ALB
resource "aws_cognito_user_pool_client" "alb" {
  name         = "${var.name_prefix}-alb-client"
  user_pool_id = aws_cognito_user_pool.main.id

  # ALB requires a client secret
  generate_secret = true

  # OAuth configuration for ALB (browser-based login)
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid"]
  allowed_oauth_flows_user_pool_client = true
  supported_identity_providers         = ["COGNITO"]

  # Enable admin auth for programmatic access (e.g., deployment testing)
  # This allows server-side code with AWS credentials to authenticate users
  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # Callback URL for ALB authentication
  # ALB uses this specific path for OAuth callbacks
  callback_urls = ["https://${var.domain_name}/oauth2/idpresponse"]

  # Prevent token leakage
  prevent_user_existence_errors = "ENABLED"
}

# Outputs
output "user_pool_arn" {
  description = "ARN of the Cognito user pool"
  value       = aws_cognito_user_pool.main.arn
}

output "user_pool_id" {
  description = "ID of the Cognito user pool (for managing users via CLI)"
  value       = aws_cognito_user_pool.main.id
}

output "client_id" {
  description = "Client ID for ALB authentication"
  value       = aws_cognito_user_pool_client.alb.id
}

output "client_secret" {
  description = "Client secret for ALB authentication"
  value       = aws_cognito_user_pool_client.alb.client_secret
  sensitive   = true
}

output "domain" {
  description = "Cognito domain prefix (for ALB authentication config)"
  value       = aws_cognito_user_pool_domain.main.domain
}

output "domain_url" {
  description = "Full Cognito domain URL"
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${data.aws_region.current.id}.amazoncognito.com"
}

data "aws_region" "current" {}
