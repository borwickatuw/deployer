# ------------------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------------------

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "alb_arn" {
  description = "ARN of the ALB to protect"
  type        = string
}

# ------------------------------------------------------------------------------
# Rule Configuration
# ------------------------------------------------------------------------------

variable "ip_reputation_enabled" {
  description = "Enable AWS IP reputation list (blocks known malicious IPs)"
  type        = bool
  default     = true
}

variable "common_rules_enabled" {
  description = "Enable AWS Common Rule Set (OWASP Top 10 protection)"
  type        = bool
  default     = true
}

variable "known_bad_inputs_enabled" {
  description = "Enable Known Bad Inputs rule set (blocks exploit patterns)"
  type        = bool
  default     = true
}

variable "sqli_rules_enabled" {
  description = "Enable SQL injection protection rules"
  type        = bool
  default     = false
}

variable "common_rules_excluded" {
  description = "Rules to exclude from AWSManagedRulesCommonRuleSet (e.g., ['SizeRestrictions_BODY'] to allow file uploads)"
  type        = list(string)
  default     = []
}

# ------------------------------------------------------------------------------
# Rate Limiting
# ------------------------------------------------------------------------------

variable "rate_limit_enabled" {
  description = "Enable rate limiting per IP address"
  type        = bool
  default     = true
}

variable "rate_limit_requests" {
  description = "Maximum requests per IP in the evaluation window"
  type        = number
  default     = 2000
}

variable "rate_limit_window" {
  description = "Rate limit evaluation window in seconds (60, 120, 300, or 600)"
  type        = number
  default     = 300

  validation {
    condition     = contains([60, 120, 300, 600], var.rate_limit_window)
    error_message = "rate_limit_window must be 60, 120, 300, or 600 seconds"
  }
}

# ------------------------------------------------------------------------------
# Bot Control (paid tier)
# ------------------------------------------------------------------------------

variable "bot_control_level" {
  description = "Bot control level: 'none', 'common' (basic protection), or 'targeted' (advanced, more expensive)"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "common", "targeted"], var.bot_control_level)
    error_message = "bot_control_level must be 'none', 'common', or 'targeted'"
  }
}

variable "bot_control_scope_paths" {
  description = "URL path prefixes where bot control applies (empty = all paths)"
  type        = list(string)
  default     = []
}

# ------------------------------------------------------------------------------
# Geographic and IP Controls
# ------------------------------------------------------------------------------

variable "geo_block_countries" {
  description = "ISO 3166-1 alpha-2 country codes to block (e.g., ['RU', 'CN', 'KP'])"
  type        = list(string)
  default     = []
}

variable "ip_allowlist" {
  description = "CIDR blocks to always allow (bypasses all rules)"
  type        = list(string)
  default     = []
}

variable "health_check_paths" {
  description = "URL paths to exclude from WAF checks (for load balancer health checks)"
  type        = list(string)
  default     = []
}

# ------------------------------------------------------------------------------
# Action Configuration
# ------------------------------------------------------------------------------

variable "default_action" {
  description = "Default action when no rule matches: 'allow' or 'block'"
  type        = string
  default     = "allow"

  validation {
    condition     = contains(["allow", "block"], var.default_action)
    error_message = "default_action must be 'allow' or 'block'"
  }
}

variable "rule_action_override" {
  description = "Override rule actions: 'none' (normal), 'count' (log only, don't block)"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "count"], var.rule_action_override)
    error_message = "rule_action_override must be 'none' or 'count'"
  }
}

# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------

variable "logging_enabled" {
  description = "Enable CloudWatch logging for WAF"
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 365
}
