variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "node_type" {
  type    = string
  default = "cache.t3.micro"
}

variable "ecs_security_group" {
  description = "Security group ID for ECS tasks (allowed to connect)"
  type        = string
}

variable "snapshot_retention_limit" {
  description = "Number of days to retain automatic snapshots. Set to 0 to disable. Requires cache.t3.small or larger (cache.t3.micro does not support backups)."
  type        = number
  default     = 1
}

# Subnet group
resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.name_prefix}-cache-subnet"
  subnet_ids = var.subnet_ids

  tags = {
    Name = "${var.name_prefix}-cache-subnet"
  }
}

# Security group
resource "aws_security_group" "elasticache" {
  name        = "${var.name_prefix}-elasticache"
  description = "Security group for ElastiCache"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Redis access from ECS tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [var.ecs_security_group]
  }

  tags = {
    Name = "${var.name_prefix}-elasticache-sg"
  }
}

resource "aws_elasticache_cluster" "main" {
  cluster_id           = "${var.name_prefix}-cache"
  engine               = "redis"
  node_type            = var.node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.0"
  port                 = 6379

  snapshot_retention_limit = var.snapshot_retention_limit

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.elasticache.id]

  tags = {
    Name = "${var.name_prefix}-cache"
  }
}

# Outputs
output "endpoint" {
  value = aws_elasticache_cluster.main.cache_nodes[0].address
}

output "port" {
  value = aws_elasticache_cluster.main.cache_nodes[0].port
}

output "connection_url" {
  value = "redis://${aws_elasticache_cluster.main.cache_nodes[0].address}:${aws_elasticache_cluster.main.cache_nodes[0].port}"
}

output "security_group_id" {
  value = aws_security_group.elasticache.id
}
