# AWS WAF Web Application Firewall
#
# Provides protection against common web exploits and bot traffic.
# Attaches to an ALB and evaluates all incoming requests.
#
# Features:
# - AWS Managed Rules for OWASP Top 10 protection
# - IP reputation blocking (known malicious IPs)
# - Rate limiting per IP address
# - Optional Bot Control (paid tier)
# - Optional geographic blocking
# - IP allowlist for trusted sources
# - CloudWatch logging for debugging
#
# Cost estimate (monthly):
# - Base: ~$15-20 (Web ACL + managed rules + minimal traffic)
# - With Bot Control Common: ~$35
# - With Bot Control Targeted: ~$50+

terraform {
  required_version = ">= 1.6.0"
}

# ------------------------------------------------------------------------------
# IP Set for Allowlist
# ------------------------------------------------------------------------------

resource "aws_wafv2_ip_set" "allowlist" {
  count = length(var.ip_allowlist) > 0 ? 1 : 0

  name               = "${var.name_prefix}-allowlist"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = var.ip_allowlist

  tags = {
    Name = "${var.name_prefix}-waf-allowlist"
  }
}

# ------------------------------------------------------------------------------
# Web ACL
# ------------------------------------------------------------------------------

resource "aws_wafv2_web_acl" "main" {
  name        = "${var.name_prefix}-waf"
  scope       = "REGIONAL"
  description = "WAF for ${var.name_prefix}"

  default_action {
    dynamic "allow" {
      for_each = var.default_action == "allow" ? [1] : []
      content {}
    }
    dynamic "block" {
      for_each = var.default_action == "block" ? [1] : []
      content {}
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${replace(var.name_prefix, "-", "")}WAF"
    sampled_requests_enabled   = true
  }

  # ---------------------------------------------------------------------------
  # IP Allowlist (highest priority - bypass all rules)
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = length(var.ip_allowlist) > 0 ? [1] : []
    content {
      name     = "ip-allowlist"
      priority = local.priority_ip_allowlist

      action {
        allow {}
      }

      statement {
        ip_set_reference_statement {
          arn = aws_wafv2_ip_set.allowlist[0].arn
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}IPAllowlist"
        sampled_requests_enabled   = true
      }
    }
  }

  # ---------------------------------------------------------------------------
  # Health Check Bypass
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = length(var.health_check_paths) > 0 ? [1] : []
    content {
      name     = "allow-health-checks"
      priority = local.priority_health_check

      action {
        allow {}
      }

      statement {
        or_statement {
          dynamic "statement" {
            for_each = var.health_check_paths
            content {
              byte_match_statement {
                search_string         = statement.value
                positional_constraint = "STARTS_WITH"
                field_to_match {
                  uri_path {}
                }
                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}HealthCheck"
        sampled_requests_enabled   = false
      }
    }
  }

  # ---------------------------------------------------------------------------
  # Rate Limiting
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = var.rate_limit_enabled ? [1] : []
    content {
      name     = "rate-limit"
      priority = local.priority_rate_limit

      action {
        dynamic "block" {
          for_each = local.use_count_mode ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = local.use_count_mode ? [1] : []
          content {}
        }
      }

      statement {
        rate_based_statement {
          limit                 = var.rate_limit_requests
          evaluation_window_sec = var.rate_limit_window
          aggregate_key_type    = "IP"
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}RateLimit"
        sampled_requests_enabled   = true
      }
    }
  }

  # ---------------------------------------------------------------------------
  # Geographic Blocking
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = length(var.geo_block_countries) > 0 ? [1] : []
    content {
      name     = "geo-block"
      priority = local.priority_geo_block

      action {
        dynamic "block" {
          for_each = local.use_count_mode ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = local.use_count_mode ? [1] : []
          content {}
        }
      }

      statement {
        geo_match_statement {
          country_codes = var.geo_block_countries
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}GeoBlock"
        sampled_requests_enabled   = true
      }
    }
  }

  # ---------------------------------------------------------------------------
  # AWS Managed Rules: IP Reputation List
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = var.ip_reputation_enabled ? [1] : []
    content {
      name     = "aws-ip-reputation"
      priority = local.priority_ip_reputation

      override_action {
        dynamic "none" {
          for_each = local.use_count_mode ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = local.use_count_mode ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = "AWSManagedRulesAmazonIpReputationList"
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}IPReputation"
        sampled_requests_enabled   = true
      }
    }
  }

  # ---------------------------------------------------------------------------
  # AWS Managed Rules: Common Rule Set (OWASP Top 10)
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = var.common_rules_enabled ? [1] : []
    content {
      name     = "aws-common-rules"
      priority = local.priority_common_rules

      override_action {
        dynamic "none" {
          for_each = local.use_count_mode ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = local.use_count_mode ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = "AWSManagedRulesCommonRuleSet"

          # Exclude specific rules (e.g., SizeRestrictions_BODY to allow file uploads)
          dynamic "rule_action_override" {
            for_each = var.common_rules_excluded
            content {
              name = rule_action_override.value
              action_to_use {
                count {}
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}CommonRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ---------------------------------------------------------------------------
  # AWS Managed Rules: Known Bad Inputs
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = var.known_bad_inputs_enabled ? [1] : []
    content {
      name     = "aws-known-bad-inputs"
      priority = local.priority_known_bad_inputs

      override_action {
        dynamic "none" {
          for_each = local.use_count_mode ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = local.use_count_mode ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = "AWSManagedRulesKnownBadInputsRuleSet"
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}KnownBadInputs"
        sampled_requests_enabled   = true
      }
    }
  }

  # ---------------------------------------------------------------------------
  # AWS Managed Rules: SQL Injection
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = var.sqli_rules_enabled ? [1] : []
    content {
      name     = "aws-sqli-rules"
      priority = local.priority_sqli_rules

      override_action {
        dynamic "none" {
          for_each = local.use_count_mode ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = local.use_count_mode ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = "AWSManagedRulesSQLiRuleSet"
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}SQLi"
        sampled_requests_enabled   = true
      }
    }
  }

  # ---------------------------------------------------------------------------
  # AWS Managed Rules: Bot Control (paid tier)
  # ---------------------------------------------------------------------------

  dynamic "rule" {
    for_each = local.bot_control_enabled ? [1] : []
    content {
      name     = "aws-bot-control"
      priority = local.priority_bot_control

      override_action {
        dynamic "none" {
          for_each = local.use_count_mode ? [] : [1]
          content {}
        }
        dynamic "count" {
          for_each = local.use_count_mode ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = "AWSManagedRulesBotControlRuleSet"

          managed_rule_group_configs {
            aws_managed_rules_bot_control_rule_set {
              inspection_level = var.bot_control_level == "targeted" ? "TARGETED" : "COMMON"
            }
          }

          # Scope down to specific paths if configured (reduces cost)
          dynamic "scope_down_statement" {
            for_each = length(var.bot_control_scope_paths) > 0 ? [1] : []
            content {
              or_statement {
                dynamic "statement" {
                  for_each = var.bot_control_scope_paths
                  content {
                    byte_match_statement {
                      search_string         = statement.value
                      positional_constraint = "STARTS_WITH"
                      field_to_match {
                        uri_path {}
                      }
                      text_transformation {
                        priority = 0
                        type     = "LOWERCASE"
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${replace(var.name_prefix, "-", "")}BotControl"
        sampled_requests_enabled   = true
      }
    }
  }

  tags = {
    Name = "${var.name_prefix}-waf"
  }
}

# ------------------------------------------------------------------------------
# ALB Association
# ------------------------------------------------------------------------------

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = var.alb_arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}
