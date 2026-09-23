#!/usr/bin/env bash
#
# Run Checkov over each OpenTofu directory given as an argument, with the one
# skip list and the one scanner pin the whole deployer pair uses.
#
# Usage:
#   bin/checkov-scan.sh DIR [DIR...]
#
# Callers:
#   deployer              `make security-checkov` scans `.` (modules, the root
#                         module, environments/deployer.tf)
#   deployer-environments `make security-checkov` scans each environment
#                         directory, reaching this script through its root
#                         main.tf symlink
#
# Each directory is a separate root, so they are scanned one at a time rather
# than as one tree. local/ and .terraform/ are gitignored scratch and are
# excluded so a scan is reproducible across machines; external modules are not
# downloaded because every module source here is a local path.
set -euo pipefail

# Every suppression, one per line, with its reason. A check that stops firing
# comes off this list: re-run with the list emptied (SKIP_CHECKS=()) over
# both repos and compare the failing ids against what is here.
SKIP_CHECKS=(
    # KMS encryption not needed (SSE-S3/default sufficient for our use case)
    CKV_AWS_145 # S3 KMS
    CKV_AWS_158 # CloudWatch KMS
    CKV_AWS_136 # ECR KMS
    CKV_AWS_26  # SNS KMS
    CKV_AWS_173 # Lambda env KMS
    CKV_AWS_354 # RDS Performance Insights KMS

    # Intentional network design
    CKV_AWS_130 # public subnets for the ALB
    CKV_AWS_260 # ALB ingress on port 80
    CKV_AWS_382 # ECS egress 0.0.0.0/0 for ECR/CloudWatch/Secrets Manager
    CKV_AWS_378 # ALB target group HTTP -- TLS terminates at the ALB

    # CloudFront design choices
    CKV_AWS_86  # CF access logging
    CKV_AWS_68  # CF WAF -- WAF is on shared infra
    CKV_AWS_310 # CF origin failover
    CKV_AWS_305 # CF default root object
    CKV_AWS_374 # CF geo restriction
    CKV2_AWS_32 # CF response headers policy
    CKV2_AWS_47 # CF WAF Log4j -- no Java

    # Lambda scheduler (low-risk internal function)
    CKV_AWS_115 # concurrency limit
    CKV_AWS_116 # DLQ
    CKV_AWS_117 # VPC
    CKV_AWS_50  # X-Ray
    CKV_AWS_272 # code signing

    # S3 features not needed
    CKV_AWS_144 # cross-region replication
    CKV_AWS_18  # access logging
    CKV2_AWS_61 # lifecycle configuration
    CKV2_AWS_62 # event notifications
    CKV_AWS_21  # versioning -- configurable per bucket via variable

    # Intentional design / false positives
    CKV2_AWS_5  # SG attachment -- the ECS SG is attached at runtime
    CKV2_AWS_19 # EIP attachment -- the NAT gateway EIP
    CKV2_AWS_12 # default VPC SG
    CKV2_AWS_23 # Route53 A record
    CKV2_AWS_28 # ALB WAF -- WAF is on CloudFront
    CKV2_AWS_57 # Secrets Manager rotation
    CKV2_AWS_6  # S3 public access block -- present, but conditional on var.public

    # Variable-dependent (Checkov cannot evaluate variables)
    CKV_AWS_150 # ALB deletion protection -- var.deletion_protection

    # VPC flow logs IAM policy (Resource=* required for CloudWatch Logs)
    CKV_AWS_290 # IAM write without constraints
    CKV_AWS_355 # IAM * resource

    # Deferred -- valid findings that need infrastructure changes
    CKV_AWS_161 # RDS IAM auth -- the apps use a password; needs app changes first
    CKV_AWS_157 # RDS Multi-AZ -- set per environment (rds_multi_az)
    CKV_AWS_293 # RDS deletion protection -- set per environment (rds_deletion_protection)
    CKV_AWS_149 # Secrets Manager CMK
    CKV_AWS_51  # ECR immutable tags -- the deploy workflow pushes `latest`

    # S3 public access block -- conditional on var.public
    CKV_AWS_53
    CKV_AWS_54
    CKV_AWS_55
    CKV_AWS_56

    # CI role statements need Resource=* (AWS API design, not restrictable):
    # ecs:Describe*, ecs:RegisterTaskDefinition, ecr:GetAuthorizationToken,
    # elasticloadbalancing:Describe*, ssm:DescribeParameters,
    # sts:GetCallerIdentity
    CKV_AWS_356

    # Infra admin roles (bootstrap module) -- broad permissions by design
    CKV_AWS_109 # permissions management
    CKV_AWS_111 # write without constraints
    CKV_AWS_107 # credentials exposure

    # IAM user policy for role assumption (intentional pattern, single user)
    CKV_AWS_40

    # WAF Log4j2 rule -- no Java apps in this infrastructure
    CKV_AWS_192
)

# Pinned exactly, and only here: deployer-environments has no pyproject.toml
# and so no lockfile to hold the scanner, and it runs this script rather than
# its own copy, so this pin is the pair's one pin. An unpinned `uvx checkov`
# lets an upstream release turn the gate red for reasons unrelated to the
# change being made, and makes a recorded "N passed / 0 failed"
# unreproducible. Bump during the comprehensive review (`uvx checkov
# --version` shows what is current) and re-run `make security-checkov` in both
# repos.
CHECKOV_VERSION=3.3.19

if [ "$#" -eq 0 ]; then
    echo "Usage: bin/checkov-scan.sh DIR [DIR...]" >&2
    echo "Refusing to run with no directories: the scan would pass vacuously." >&2
    exit 1
fi

skip_arg="$(
    IFS=,
    echo "${SKIP_CHECKS[*]}"
)"

echo "=== Checkov IaC Security Scanner ==="
for dir in "$@"; do
    echo "--- Scanning $dir ---"
    uvx "checkov==${CHECKOV_VERSION}" --directory "$dir" --framework terraform \
        --download-external-modules false --compact --quiet \
        --skip-path 'local/' --skip-path '\.terraform/' \
        --skip-check "$skip_arg"
done
echo "=== Checkov Complete ==="
