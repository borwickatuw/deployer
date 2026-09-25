# ECS GPU Capacity
#
# One fixed EC2 GPU instance registered as a container instance of the
# ECS cluster, for services that need a card (a task with a GPU
# resourceRequirement; deploy.toml `gpu = 1`, placed with the EC2 launch
# type). The instance lives on the environment's start/stop schedule:
# modules/staging-scheduler (and bin/environment.py) stop it after the
# services have scaled to zero and start it before they scale back up.
# Stopped, it costs only its volume — and that volume is the point. The
# disk the instance built up (the worker image's tens of gigabytes of
# layers, the deployer's build cache) is the disk it starts with, so the
# morning start is a boot and an agent reconnect, not a pull.
#
# The instance is also the build host for the image it runs: its Docker
# daemon is exposed on loopback only, and the deployer reaches it over an
# SSM port-forwarding session (deploy/build_host.py). No key pair, no
# ingress rule, a private subnet, IMDSv2 required with hop limit 1: the
# only way in is ssm:StartSession, which the bootstrap scopes by the
# deployer-build-host tag.

variable "name_prefix" {
  description = "Prefix for resource names (e.g., myapp-staging)"
  type        = string
}

variable "cluster_name" {
  description = "ECS cluster the instance registers with"
  type        = string
}

variable "vpc_id" {
  description = "VPC the instance runs in"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets; the instance takes the first"
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
# The instance: the ECS GPU-optimized AMI, resolved at apply from the
# public SSM parameter. First-boot user data writes the agent config and
# the Docker loopback proxy; both live on the root volume, so they
# survive every stop and start.
# ------------------------------------------------------------------------------

data "aws_ssm_parameter" "ecs_gpu_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended/image_id"
}

locals {
  ecs_config_lines = concat(
    [
      "ECS_CLUSTER=${var.cluster_name}",
      "ECS_ENABLE_GPU_SUPPORT=true",
      # A task must act as its own role, never as the instance
      "ECS_AWSVPC_BLOCK_IMDS=true",
      # The whole point of keeping the disk: use the layers already here
      "ECS_IMAGE_PULL_BEHAVIOR=prefer-cached", # pragma: allowlist secret
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

resource "aws_instance" "gpu" {
  ami                  = data.aws_ssm_parameter.ecs_gpu_ami.value
  instance_type        = var.instance_type
  subnet_id            = var.private_subnet_ids[0]
  iam_instance_profile = aws_iam_instance_profile.instance.name

  vpc_security_group_ids = [aws_security_group.instance.id]

  # First boot only; a change to it is a new box (the config it writes is
  # on the volume, so an edited script would otherwise never run)
  user_data                   = local.user_data
  user_data_replace_on_change = true

  ebs_optimized = true
  monitoring    = true

  # The schedule stops it; nothing here should ever terminate it
  instance_initiated_shutdown_behavior = "stop"

  # No key pair: the only way in is an SSM session
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  root_block_device {
    volume_size           = var.volume_gb
    volume_type           = "gp3"
    throughput            = var.volume_throughput
    encrypted             = true
    delete_on_termination = true

    tags = {
      Name = "${var.name_prefix}-gpu"
    }
  }

  tags = {
    Name = "${var.name_prefix}-gpu"
    # What the deploy role's ssm:StartSession and the scheduler's
    # ec2:Start/StopInstances are scoped by (bootstrap)
    "deployer-build-host" = var.name_prefix
  }

  # The SSM parameter moves with every AMI release; the instance's disk is
  # its cache, so an AMI roll is a deliberate replacement
  # (tofu apply -replace=module.ecs_gpu_capacity[0].aws_instance.gpu),
  # and the next start after one is cold.
  lifecycle {
    ignore_changes = [ami]
  }
}

# Outputs
output "instance_id" {
  value = aws_instance.gpu.id
}

output "instance_role_arn" {
  value = aws_iam_role.instance.arn
}

output "security_group_id" {
  value = aws_security_group.instance.id
}
