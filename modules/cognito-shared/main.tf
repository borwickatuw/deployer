# Shared Cognito User Pool for Multiple Applications
#
# Creates a single user pool with multiple app clients (one per domain).
# This allows users to have one set of credentials for all staging environments.
#
# Usage:
#   module "cognito_shared" {
#     source      = "../modules/cognito-shared"
#     name_prefix = "staging-shared"
#     app_domains = {
#       myapp      = "myapp-staging.example.com"
#       otherapp   = "otherapp-staging.example.com"
#     }
#   }

variable "name_prefix" {
  description = "Prefix for resource names (e.g., 'staging-shared')"
  type        = string
}

variable "app_domains" {
  description = "Map of app name to domain (e.g., { myapp = 'myapp-staging.example.com' })"
  type        = map(string)
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}

# User Pool
resource "aws_cognito_user_pool" "shared" {
  name = "${var.name_prefix}-users"

  # Only admins can create users - no self-signup
  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_subject = "${var.name_prefix} - Your staging access credentials"
      email_message = "You have been granted access to the ${var.name_prefix} staging environments.\n\nUsername: {username}\nTemporary password: {####}\n\nPlease log in and change your password."
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
resource "aws_cognito_user_pool_domain" "shared" {
  domain       = var.name_prefix
  user_pool_id = aws_cognito_user_pool.shared.id
}

# One client per app
resource "aws_cognito_user_pool_client" "app" {
  for_each     = var.app_domains
  name         = "${var.name_prefix}-${each.key}-client"
  user_pool_id = aws_cognito_user_pool.shared.id

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
  callback_urls = ["https://${each.value}/oauth2/idpresponse"]

  # Prevent token leakage
  prevent_user_existence_errors = "ENABLED"
}

# Outputs
output "user_pool_id" {
  description = "ID of the shared Cognito user pool (for managing users via CLI)"
  value       = aws_cognito_user_pool.shared.id
}

output "user_pool_arn" {
  description = "ARN of the shared Cognito user pool"
  value       = aws_cognito_user_pool.shared.arn
}

output "domain" {
  description = "Cognito domain prefix (for ALB authentication config)"
  value       = aws_cognito_user_pool_domain.shared.domain
}

output "domain_url" {
  description = "Full Cognito domain URL"
  value       = "https://${aws_cognito_user_pool_domain.shared.domain}.auth.${data.aws_region.current.id}.amazoncognito.com"
}

output "app_clients" {
  description = "Map of app name to client configuration"
  value = { for app, client in aws_cognito_user_pool_client.app : app => {
    client_id     = client.id
    client_secret = client.client_secret
  } }
  sensitive = true
}

data "aws_region" "current" {}
