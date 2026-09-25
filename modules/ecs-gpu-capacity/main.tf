# ECS GPU Capacity
#
# One EC2 Auto Scaling group of GPU instances behind an ECS capacity
# provider, for services that need a card (a task with a GPU
# resourceRequirement; deploy.toml `gpu = 1`). The first posture is one
# instance on the environment's start/stop schedule: the scheduler sets
# the service's desiredCount and the capacity provider's managed scaling
# launches or retires the instance to match. A warm pool keeps the
# retired instance STOPPED between working days rather than terminated,
# so its disk — tens of gigabytes of image layers — is still there the
# next morning and the daily start is a boot, not a pull.
#
# The instance is also the build host for the image it runs: its Docker
# daemon is exposed on loopback only, and the deployer reaches it over an
# SSM port-forwarding session (deploy/build_host.py). No key pair, no
# ingress rule, private subnets, IMDSv2 required with hop limit 1: the
# only way in is ssm:StartSession, which the bootstrap scopes by the
# deployer-build-host tag.

variable "name_prefix" {
  description = "Prefix for resource names (e.g., myapp-staging)"
  type        = string
}

variable "cluster_name" {
  description = "ECS cluster the instances register with"
  type        = string
}

variable "vpc_id" {
  description = "VPC the instances run in"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets the Auto Scaling group may launch into (every AZ helps GPU launch scarcity)"
  type        = list(string)
}

variable "instance_type" {
  description = "GPU instance type (e.g., g5.2xlarge — one A10G)"
  type        = string
}

variable "volume_gb" {
  description = "Root volume size in GB; sized for the image layers the box keeps between days"
  type        = number
  default     = 300
}

variable "volume_throughput" {
  description = "gp3 root volume throughput in MiB/s (image pulls and layer extraction are disk-bound)"
  type        = number
  default     = 250
}

variable "permissions_boundary" {
  description = "ARN of the container-instance permissions boundary (bootstrap's ecs_instance_role_boundary_arn)"
  type        = string
}

variable "ecs_agent_settings" {
  description = "Extra KEY=VALUE lines appended to /etc/ecs/ecs.config"
  type        = list(string)
  default     = []
}

data "aws_caller_identity" "current" {}

# ------------------------------------------------------------------------------
# Instance role: the ECS agent and the SSM agent, nothing a task does
# ------------------------------------------------------------------------------

data "aws_iam_policy_document" "instance_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name                 = "${var.name_prefix}-gpu-instance"
  assume_role_policy   = data.aws_iam_policy_document.instance_trust.json
  permissions_boundary = var.permissions_boundary

  tags = {
    Name = "${var.name_prefix}-gpu-instance"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_for_ec2" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-gpu-instance"
  role = aws_iam_role.instance.name
}

# ------------------------------------------------------------------------------
# Security group: egress only. Tasks run in awsvpc mode with the cluster's
# own task security group; this one is the instance ENI's.
# ------------------------------------------------------------------------------

resource "aws_security_group" "instance" {
  name        = "${var.name_prefix}-gpu-instance"
  description = "GPU container instance: egress only (ECR, SSM, logs)"
  vpc_id      = var.vpc_id

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name_prefix}-gpu-instance-sg"
  }
}

# ------------------------------------------------------------------------------
# Launch template: the ECS GPU-optimized AMI, resolved at apply from the
# public SSM parameter so an AMI roll is an instance refresh, not an edit.
# ------------------------------------------------------------------------------

locals {
  ecs_config_lines = concat(
    [
      "ECS_CLUSTER=${var.cluster_name}",
      "ECS_ENABLE_GPU_SUPPORT=true",
      # The agent checks in with the warm pool before the instance is
      # stopped, so a warmed instance registers on start
      "ECS_WARM_POOLS_CHECK=true",
      # A task must act as its own role, never as the instance
      "ECS_AWSVPC_BLOCK_IMDS=true",
      # The whole point of keeping the disk: use the layers already here
      "ECS_IMAGE_PULL_BEHAVIOR=prefer-cached",
    ],
    var.ecs_agent_settings,
  )

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail

    cat >> /etc/ecs/ecs.config <<'ECSCONFIG'
    ${join("\n    ", local.ecs_config_lines)}
    ECSCONFIG

    # The Docker API on loopback only, for the deployer's SSM port-forward:
    # systemd-socket-proxyd relays 127.0.0.1:2375 to the daemon's unix
    # socket. Nothing off-box can reach it; the session is the only door.
    cat > /etc/systemd/system/docker-loopback.socket <<'UNIT'
    [Unit]
    Description=Docker API on loopback for the deployer build tunnel

    [Socket]
    ListenStream=127.0.0.1:2375

    [Install]
    WantedBy=sockets.target
    UNIT

    cat > /etc/systemd/system/docker-loopback.service <<'UNIT'
    [Unit]
    Description=Relay loopback Docker API to the daemon socket
    Requires=docker-loopback.socket docker.service
    After=docker.service

    [Service]
    ExecStart=/usr/lib/systemd/systemd-socket-proxyd /var/run/docker.sock
    UNIT

    systemctl daemon-reload
    systemctl enable --now docker-loopback.socket
  EOT
}

resource "aws_launch_template" "gpu" {
  name          = "${var.name_prefix}-gpu"
  description   = "ECS GPU container instance (${var.instance_type})"
  image_id      = "resolve:ssm:/aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended/image_id"
  instance_type = var.instance_type
  # Every launch takes the newest version, so an AMI roll or a user-data
  # change reaches the next instance without a per-launch pin
  update_default_version = true

  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }

  vpc_security_group_ids = [aws_security_group.instance.id]

  # No key pair: the only way in is an SSM session
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.volume_gb
      volume_type           = "gp3"
      throughput            = var.volume_throughput
      encrypted             = true
      delete_on_termination = true
    }
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(local.user_data)

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.name_prefix}-gpu"
      # What the deploy role's ssm:StartSession is scoped by (bootstrap)
      "deployer-build-host" = var.name_prefix
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name = "${var.name_prefix}-gpu"
    }
  }

  tags = {
    Name = "${var.name_prefix}-gpu"
  }
}

# ------------------------------------------------------------------------------
# Auto Scaling group: min 0 / max 1, managed by the capacity provider.
# The warm pool stops a retired instance instead of terminating it; with
# reuse_on_scale_in the same instance goes back to the pool on scale-in,
# so the disk it built up is the disk it starts with. No mixed-instances
# policy: warm pools do not support one.
# ------------------------------------------------------------------------------

resource "aws_autoscaling_group" "gpu" {
  name                = "${var.name_prefix}-gpu"
  min_size            = 0
  max_size            = 1
  desired_capacity    = 0
  vpc_zone_identifier = var.private_subnet_ids
  # The capacity provider's managed termination protection needs this
  protect_from_scale_in = true

  launch_template {
    id      = aws_launch_template.gpu.id
    version = "$Latest"
  }

  warm_pool {
    pool_state                  = "Stopped"
    min_size                    = 0
    max_group_prepared_capacity = 1

    instance_reuse_policy {
      reuse_on_scale_in = true
    }
  }

  # ECS managed scaling drives desired_capacity; tofu must not fight it
  lifecycle {
    ignore_changes = [desired_capacity]
  }

  tag {
    key                 = "Name"
    value               = "${var.name_prefix}-gpu"
    propagate_at_launch = true
  }

  # Required for ECS managed scaling to act on this group
  tag {
    key                 = "AmazonECSManaged"
    value               = ""
    propagate_at_launch = true
  }
}

resource "aws_ecs_capacity_provider" "gpu" {
  name = "${var.name_prefix}-gpu"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.gpu.arn
    managed_termination_protection = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
      # A GPU box takes minutes to join; do not scale twice for one task
      instance_warmup_period = 300
    }
  }

  tags = {
    Name = "${var.name_prefix}-gpu"
  }
}

# Outputs
output "capacity_provider_name" {
  value = aws_ecs_capacity_provider.gpu.name
}

output "asg_name" {
  value = aws_autoscaling_group.gpu.name
}

output "instance_role_arn" {
  value = aws_iam_role.instance.arn
}

output "security_group_id" {
  value = aws_security_group.instance.id
}
