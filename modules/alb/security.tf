# ------------------------------------------------------------------------------
# Security Group
# ------------------------------------------------------------------------------

# CloudFront's origin-facing address ranges, maintained by AWS. Read only when
# ingress is restricted to CloudFront.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  count = var.restrict_ingress_to_cloudfront ? 1 : 0

  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb"
  description = "Security group for Application Load Balancer"
  vpc_id      = var.vpc_id

  # With ingress restricted to CloudFront, port 80 stays closed: CloudFront
  # reaches this origin over HTTPS only (modules/cloudfront-alb), and the
  # prefix list weighs 55 of the default 60 inbound rules per security group,
  # so it can be referenced once, not once per port.
  dynamic "ingress" {
    for_each = var.restrict_ingress_to_cloudfront ? [] : [1]
    content {
      description = "HTTP"
      from_port   = 80
      to_port     = 80
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  ingress {
    description     = var.restrict_ingress_to_cloudfront ? "HTTPS from CloudFront" : "HTTPS"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    cidr_blocks     = var.restrict_ingress_to_cloudfront ? [] : ["0.0.0.0/0"]
    prefix_list_ids = var.restrict_ingress_to_cloudfront ? [data.aws_ec2_managed_prefix_list.cloudfront_origin_facing[0].id] : []
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name_prefix}-alb-sg"
  }

  lifecycle {
    precondition {
      condition     = !var.restrict_ingress_to_cloudfront || var.certificate_arn != null
      error_message = "restrict_ingress_to_cloudfront requires certificate_arn: CloudFront reaches the ALB over HTTPS only, and the restriction closes port 80."
    }
  }
}
