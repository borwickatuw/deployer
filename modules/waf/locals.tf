# ------------------------------------------------------------------------------
# Locals
# ------------------------------------------------------------------------------

locals {
  bot_control_enabled = var.bot_control_level != "none"
  use_count_mode      = var.rule_action_override == "count"

  # Priority assignments (lower = higher priority, evaluated first)
  # 0-9: Allowlists (highest priority)
  # 10-19: Health checks
  # 20-29: Rate limiting
  # 30-39: Geographic blocking
  # 40-99: Managed rules
  priority_ip_allowlist     = 1
  priority_health_check     = 10
  priority_rate_limit       = 20
  priority_geo_block        = 30
  priority_ip_reputation    = 40
  priority_common_rules     = 50
  priority_known_bad_inputs = 60
  priority_sqli_rules       = 70
  priority_bot_control      = 80
}
