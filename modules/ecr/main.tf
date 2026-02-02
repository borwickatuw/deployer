# ECR Repositories
#
# Creates ECR repositories for container images.
# Includes lifecycle policies to control image retention and reduce storage costs.

variable "name_prefix" {
  description = "Prefix for repository names (e.g., myapp-staging)"
  type        = string
}

variable "repository_names" {
  description = "List of repository names (will be prefixed with name_prefix)"
  type        = list(string)
}

variable "image_tag_mutability" {
  description = "Image tag mutability setting (MUTABLE or IMMUTABLE)"
  type        = string
  default     = "MUTABLE"

  validation {
    condition     = contains(["MUTABLE", "IMMUTABLE"], var.image_tag_mutability)
    error_message = "image_tag_mutability must be MUTABLE or IMMUTABLE"
  }
}

variable "scan_on_push" {
  description = "Enable image scanning on push"
  type        = bool
  default     = true
}

variable "lifecycle_policy_count" {
  description = "Number of images to keep per repository (0 to disable lifecycle policy)"
  type        = number
  default     = 10
}

variable "force_delete" {
  description = "Delete repository even if it contains images"
  type        = bool
  default     = false
}

# Create ECR repositories
resource "aws_ecr_repository" "main" {
  for_each = toset(var.repository_names)

  name                 = "${var.name_prefix}-${each.key}"
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = var.scan_on_push
  }

  tags = {
    Name = "${var.name_prefix}-${each.key}"
  }
}

# Lifecycle policy to limit number of images
resource "aws_ecr_lifecycle_policy" "main" {
  for_each = var.lifecycle_policy_count > 0 ? toset(var.repository_names) : toset([])

  repository = aws_ecr_repository.main[each.key].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the last ${var.lifecycle_policy_count} images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.lifecycle_policy_count
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# Outputs
output "repository_urls" {
  description = "Map of repository names to their URLs"
  value       = { for k, v in aws_ecr_repository.main : k => v.repository_url }
}

output "repository_arns" {
  description = "Map of repository names to their ARNs"
  value       = { for k, v in aws_ecr_repository.main : k => v.arn }
}

output "repository_names" {
  description = "Map of short names to full repository names"
  value       = { for k, v in aws_ecr_repository.main : k => v.name }
}
