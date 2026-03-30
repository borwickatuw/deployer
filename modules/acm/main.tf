# ACM Certificate with Route 53 DNS Validation
#
# Creates an SSL/TLS certificate and automatically validates it using Route 53.
# The certificate can be used with ALB for HTTPS termination.

variable "domain_name" {
  description = "Domain name for the certificate (e.g., staging.example.com)"
  type        = string
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID for DNS validation"
  type        = string
}

variable "tags" {
  description = "Additional tags for the certificate"
  type        = map(string)
  default     = {}
}

# Request the certificate
resource "aws_acm_certificate" "main" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  tags = merge(var.tags, {
    Name = var.domain_name
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Create DNS validation records in Route 53
resource "aws_route53_record" "validation" {
  for_each = {
    for dvo in aws_acm_certificate.main.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.route53_zone_id
}

# Wait for validation to complete
resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]
}

# Outputs
output "certificate_arn" {
  description = "ARN of the validated certificate"
  value       = aws_acm_certificate_validation.main.certificate_arn
}

output "domain_name" {
  description = "Domain name of the certificate"
  value       = var.domain_name
}
