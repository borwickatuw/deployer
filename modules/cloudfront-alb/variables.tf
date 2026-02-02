# ------------------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------------------

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
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
