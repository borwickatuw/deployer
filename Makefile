# deployer Makefile
#
# Usage: make [target]
# Run `make` or `make help` to see available targets.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
MAKEFLAGS += --warn-undefined-variables
MAKEFLAGS += --no-builtin-rules

# =============================================================================
# Help
# =============================================================================

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

# =============================================================================
# Setup
# =============================================================================

.PHONY: install
install: ## Install dependencies
	@uv sync

# =============================================================================
# Code Quality
# =============================================================================

.PHONY: format
format: ## Auto-format code with black and isort
	@echo "=== Running Black ==="
	@uv run black bin src tests
	@echo ""
	@echo "=== Running isort ==="
	@uv run isort bin src tests

.PHONY: lint
lint: ## Check code formatting (black, isort)
	@echo "=== Checking Black Formatting ==="
	@uv run black --check bin src tests
	@echo ""
	@echo "=== Checking isort ==="
	@uv run isort --check-only bin src tests

# =============================================================================
# Testing
# =============================================================================

.PHONY: test
test: ## Run all tests
	@echo "=== Running Tests ==="
	@uv run pytest -v

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	@echo "=== Running Tests with Coverage ==="
	@uv run pytest --cov=src --cov=bin --cov-report=term-missing

.PHONY: check
check: lint test ## Run lint and tests
	@echo ""
	@echo "=== All Checks Passed ==="

# =============================================================================
# Security
# =============================================================================

.PHONY: security
security: security-bandit security-deps security-checkov ## Run all security checks
	@echo ""
	@echo "=== Security Checks Complete ==="

.PHONY: security-bandit
security-bandit: ## Run bandit Python security linter
	@echo "=== Bandit Security Linter ==="
	@uv run bandit -c pyproject.toml -r bin src -ll

# Checkov skip-check rationale (grouped by category):
#   KMS encryption not needed (SSE-S3/default sufficient for our use case):
#     CKV_AWS_145 (S3 KMS), CKV_AWS_158 (CloudWatch KMS), CKV_AWS_136 (ECR KMS),
#     CKV_AWS_26 (SNS KMS), CKV_AWS_173 (Lambda env KMS),
#     CKV_AWS_354 (RDS Performance Insights KMS)
#   Intentional network design:
#     CKV_AWS_130 (public subnets for ALB), CKV_AWS_260 (ALB ingress port 80),
#     CKV_AWS_382 (ECS egress 0.0.0.0/0 for ECR/CloudWatch/SecretsManager),
#     CKV_AWS_378 (ALB target group HTTP - TLS terminates at ALB)
#   CloudFront design choices:
#     CKV_AWS_86 (CF access logging), CKV_AWS_68 (CF WAF - WAF is on shared infra),
#     CKV_AWS_310 (CF origin failover), CKV_AWS_305 (CF default root object),
#     CKV_AWS_374 (CF geo restriction), CKV_AWS_174 (CF TLS version),
#     CKV2_AWS_42 (CF custom SSL cert), CKV2_AWS_32 (CF response headers policy),
#     CKV2_AWS_47 (CF WAF Log4j - no Java)
#   Lambda scheduler (low-risk internal function):
#     CKV_AWS_115 (concurrent limit), CKV_AWS_116 (DLQ), CKV_AWS_117 (VPC),
#     CKV_AWS_50 (X-Ray), CKV_AWS_272 (code-signing)
#   S3 features not needed:
#     CKV_AWS_144 (cross-region replication), CKV_AWS_18 (access logging),
#     CKV2_AWS_61 (lifecycle config), CKV2_AWS_62 (event notifications),
#     CKV_AWS_21 (versioning - already configurable via variable)
#   Intentional design / false positives:
#     CKV2_AWS_5 (SG attachment - ECS SG attached at runtime),
#     CKV2_AWS_19 (EIP attachment - NAT gateway EIP),
#     CKV2_AWS_12 (default VPC SG), CKV2_AWS_23 (Route53 A record),
#     CKV2_AWS_28 (ALB WAF - WAF is on CloudFront),
#     CKV2_AWS_57 (Secrets Manager rotation), CKV2_AWS_6 (S3 public access block
#       - already have it, but conditional on var.public so Checkov can't see it)
#   Variable-dependent (Checkov can't evaluate variables):
#     CKV_AWS_150 (ALB deletion protection - var.deletion_protection),
#     CKV_AWS_91 (ALB access logging - opt-in via var.access_logs_enabled)
#   VPC flow logs IAM policy (Resource=* required for CloudWatch Logs):
#     CKV_AWS_290 (IAM write without constraints), CKV_AWS_355 (IAM * resource)
#   Deferred - need infrastructure changes (see docs/SOMEDAY-MAYBE.md):
#     CKV_AWS_161 (RDS IAM auth),
#     CKV_AWS_157 (RDS Multi-AZ - configurable per env), CKV_AWS_293 (RDS deletion
#       protection - configurable per env),
#     CKV_AWS_149 (SecretsManager CMK), CKV_AWS_51 (ECR immutable tags)
#   S3 public access block checks (CKV_AWS_53-56) - conditional on var.public:
#     CKV_AWS_53, CKV_AWS_54, CKV_AWS_55, CKV_AWS_56
#   CI role IAM statements require Resource=* (AWS API design, not restrictable):
#     CKV_AWS_356 (ecs:Describe*, ecs:RegisterTaskDefinition, ecr:GetAuthorizationToken,
#       elasticloadbalancing:Describe*, ssm:DescribeParameters, sts:GetCallerIdentity)
#   Infra admin roles (bootstrap module) — broad permissions are intentional by design:
#     CKV_AWS_109 (permissions management), CKV_AWS_111 (write without constraints),
#     CKV_AWS_107 (credentials exposure)
#   IAM user policy for role assumption (intentional pattern, single user):
#     CKV_AWS_40 (IAM policy attached to user)
#   WAF Log4j rule (no Java apps in this infrastructure):
#     CKV_AWS_192 (WAF Log4j2 rule)
CHECKOV_SKIP := CKV_AWS_145,CKV_AWS_158,CKV_AWS_136,CKV_AWS_26,CKV_AWS_173,CKV_AWS_354,CKV_AWS_130,CKV_AWS_260,CKV_AWS_382,CKV_AWS_378,CKV_AWS_86,CKV_AWS_68,CKV_AWS_310,CKV_AWS_305,CKV_AWS_374,CKV_AWS_174,CKV2_AWS_42,CKV2_AWS_32,CKV2_AWS_47,CKV_AWS_115,CKV_AWS_116,CKV_AWS_117,CKV_AWS_50,CKV_AWS_272,CKV_AWS_144,CKV_AWS_18,CKV2_AWS_61,CKV2_AWS_62,CKV_AWS_21,CKV2_AWS_5,CKV2_AWS_19,CKV2_AWS_12,CKV2_AWS_23,CKV2_AWS_28,CKV2_AWS_57,CKV2_AWS_6,CKV_AWS_150,CKV_AWS_91,CKV_AWS_290,CKV_AWS_355,CKV_AWS_161,CKV_AWS_157,CKV_AWS_293,CKV_AWS_149,CKV_AWS_51,CKV_AWS_53,CKV_AWS_54,CKV_AWS_55,CKV_AWS_56,CKV_AWS_23,CKV_AWS_356,CKV_AWS_109,CKV_AWS_111,CKV_AWS_107,CKV_AWS_40,CKV_AWS_192

.PHONY: security-checkov
security-checkov: ## Run Checkov IaC scanner on OpenTofu modules
	@echo "=== Checkov IaC Security Scanner ==="
	@uv run checkov --directory modules --framework terraform --compact --quiet \
		--skip-check $(CHECKOV_SKIP)

.PHONY: security-deps
security-deps: ## Check dependency vulnerabilities
	@echo "=== Dependency Vulnerability Scan (pip-audit) ==="
	@uv run pip-audit

# =============================================================================
# Cleanup
# =============================================================================

.PHONY: clean
clean: ## Remove build artifacts and caches
	@echo "=== Cleaning Build Artifacts ==="
	@rm -rf .pytest_cache .ruff_cache
	@rm -rf src/*.egg-info
	@rm -f .coverage
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean complete."

.PHONY: format-docs
format-docs: ## Format markdown files
	@command -v mdformat >/dev/null 2>&1 || { echo "Error: mdformat not found. Install with: uv tool install mdformat --with mdformat-gfm"; exit 1; }
	@mdformat .
