# ------------------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------------------

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "environment" {
  description = "Environment type ('staging' or 'production'); selects the default error page copy"
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be 'staging' or 'production'."
  }
}

variable "alb_dns_name" {
  description = "DNS name of the ALB to use as origin"
  type        = string
}

variable "domain_name" {
  description = "Domain name for the CloudFront distribution"
  type        = string
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID for DNS validation"
  type        = string
}

variable "error_page_content" {
  description = "Custom HTML content for 503 error page (uses default if not provided)"
  type        = string
  default     = null
}

variable "error_caching_min_ttl" {
  description = "Minimum TTL for caching error responses (seconds)"
  type        = number
  default     = 60
}

variable "viewer_ip_header_enabled" {
  description = "Attach a viewer-request CloudFront Function that sets viewer_ip_header to the viewer's IP, overwriting any client-supplied value, so a WAF on the ALB can rate-limit per viewer. Only trustworthy when the ALB accepts traffic from CloudFront alone."
  type        = bool
  default     = false
}

variable "viewer_ip_header" {
  description = "Request header the viewer-IP CloudFront Function writes (lowercase). Must match the WAF module's viewer_ip_header."
  type        = string
  default     = "x-viewer-ip"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*$", var.viewer_ip_header)) && !startswith(var.viewer_ip_header, "x-forwarded-") && !startswith(var.viewer_ip_header, "cloudfront-") && !startswith(var.viewer_ip_header, "x-amz-") && !startswith(var.viewer_ip_header, "x-edge-")
    error_message = "viewer_ip_header must be a lowercase header name (letters, digits, hyphens) and not an X-Forwarded-*, CloudFront-*, X-Amz-* or X-Edge-* header."
  }
}

variable "price_class" {
  description = "CloudFront price class"
  type        = string
  default     = "PriceClass_100" # US, Canada, Europe only

  validation {
    condition = contains([
      "PriceClass_All",
      "PriceClass_200",
      "PriceClass_100"
    ], var.price_class)
    error_message = "price_class must be PriceClass_All, PriceClass_200, or PriceClass_100"
  }
}
