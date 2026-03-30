# Route 53 DNS Records
#
# Creates DNS records in a Route 53 hosted zone.
# Supports A records (alias) and CNAME records.

variable "zone_id" {
  description = "Route 53 hosted zone ID"
  type        = string
}

variable "records" {
  description = "Map of DNS records to create"
  type = map(object({
    type = string # "A" for alias records, "CNAME" for CNAME records
    name = string # Record name (e.g., "www" or "" for apex)

    # For A (alias) records - point to ALB, CloudFront, etc.
    alias_target = optional(object({
      dns_name               = string
      zone_id                = string
      evaluate_target_health = optional(bool, true)
    }))

    # For CNAME records
    cname_value = optional(string)
    ttl         = optional(number, 300)
  }))
  default = {}

  validation {
    condition = alltrue([
      for k, v in var.records :
      (v.type == "A" && v.alias_target != null) ||
      (v.type == "CNAME" && v.cname_value != null)
    ])
    error_message = "A records require alias_target, CNAME records require cname_value"
  }
}

# A (alias) records
resource "aws_route53_record" "alias" {
  for_each = { for k, v in var.records : k => v if v.type == "A" }

  zone_id = var.zone_id
  name    = each.value.name
  type    = "A"

  alias {
    name                   = each.value.alias_target.dns_name
    zone_id                = each.value.alias_target.zone_id
    evaluate_target_health = each.value.alias_target.evaluate_target_health
  }
}

# CNAME records
resource "aws_route53_record" "cname" {
  for_each = { for k, v in var.records : k => v if v.type == "CNAME" }

  zone_id = var.zone_id
  name    = each.value.name
  type    = "CNAME"
  ttl     = each.value.ttl
  records = [each.value.cname_value]
}

# Outputs
output "record_fqdns" {
  description = "Map of record keys to their FQDNs"
  value = merge(
    { for k, v in aws_route53_record.alias : k => v.fqdn },
    { for k, v in aws_route53_record.cname : k => v.fqdn }
  )
}

output "record_names" {
  description = "Map of record keys to their names"
  value = merge(
    { for k, v in aws_route53_record.alias : k => v.name },
    { for k, v in aws_route53_record.cname : k => v.name }
  )
}
