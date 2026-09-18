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
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

# =============================================================================
# Setup
# =============================================================================

.PHONY: install
install: ## Install dependencies (incl. dev group; default-groups is [])
	@uv sync --group dev

.PHONY: lambda-deps
lambda-deps: ## Install db lambda bundle pip deps (needed once per fresh checkout)
	@sh modules/lambda-shared/install-deps.sh modules/db-users
	@sh modules/lambda-shared/install-deps.sh modules/db-on-shared-rds

# =============================================================================
# Code Quality
# =============================================================================

# Python trees the formatters and linters own. modules/lambda-shared holds the
# tracked copy of the db-* Lambda shared code, and modules/staging-scheduler's
# lambda/ is a single tracked first-party handler.py that tofu's archive_file
# zips as-is. The db-users and db-on-shared-rds lambda/ dirs are NOT here on
# purpose -- they are build-artifact directories full of pip-vendored packages
# (and ruff's extend-exclude covers them if they are ever passed).
PY_SOURCES = bin src tests modules/lambda-shared modules/staging-scheduler/lambda

.PHONY: format
format: ## Auto-format code with black and isort
	@echo "=== Running Black ==="
	@uv run black $(PY_SOURCES)
	@echo ""
	@echo "=== Running isort ==="
	@uv run isort $(PY_SOURCES)

.PHONY: ruff
ruff: ## Run ruff linter
	@echo "=== Ruff Linter ==="
	@uv run ruff check $(PY_SOURCES)

# src/ and bin/. best-practices/PYTHON.md section 14 specifies src/; bin/ is
# where the CLI entry points live and carries the same failure shapes
# (str | None reaching a non-optional parameter), so it is gated too. tests/
# stays out: monkeypatch stubs are deliberately loose.
.PHONY: pyright
pyright: ## Type-check the package and the CLI entry points
	@echo "=== Pyright ==="
	@uv run --group dev pyright src/ bin/

.PHONY: lint
lint: ## Check formatting (black, isort) and lint (ruff, pyright)
	@echo "=== Checking Black Formatting ==="
	@uv run black --check $(PY_SOURCES)
	@echo ""
	@echo "=== Checking isort ==="
	@uv run isort --check-only $(PY_SOURCES)
	@echo ""
	@echo "=== Ruff Linter ==="
	@uv run ruff check $(PY_SOURCES)
	@echo ""
	@$(MAKE) --no-print-directory pyright

# =============================================================================
# Testing
# =============================================================================

.PHONY: test
test: ## Run all tests
	@echo "=== Running Tests ==="
	@uv run pytest

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	@echo "=== Running Tests with Coverage ==="
	@uv run pytest --cov --cov-report=term-missing

.PHONY: check
check: lint test ## Run lint and tests
	@echo ""
	@echo "=== All Checks Passed ==="

# =============================================================================
# Security
# =============================================================================

.PHONY: security
security: security-bandit security-deps security-secrets security-checkov ## Run all security checks
	@echo ""
	@echo "=== Security Checks Complete ==="

.PHONY: security-bandit
security-bandit: ## Run bandit Python security linter
	@echo "=== Bandit Security Linter ==="
	@uv run bandit -c pyproject.toml -r $(filter-out tests,$(PY_SOURCES)) -ll

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
#   Deferred - need infrastructure changes
#   (see docs/someday-maybe/checkov-deferred-items.md):
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
	@uvx checkov --directory modules --framework terraform --compact --quiet \
		--skip-check $(CHECKOV_SKIP)

.PHONY: security-deps
security-deps: ## Check dependency vulnerabilities
	@echo "=== Dependency Vulnerability Scan (uv audit) ==="
	@uv audit

.PHONY: security-secrets
security-secrets: ## Check tracked files for secrets not in .secrets.baseline
	@echo "=== Secrets Scan (detect-secrets) ==="
	@test -f .secrets.baseline || { echo "Error: .secrets.baseline missing. Bootstrap with 'make security-secrets-init' and review before committing."; exit 1; }
	@uv run --group dev detect-secrets-hook --baseline .secrets.baseline $$(git ls-files)

# detect-secrets rewrites the baseline in place with `--baseline`, so no shell
# redirect is involved: a plain `scan > .secrets.baseline` truncates the file
# before the scanner runs, and under `.SHELLFLAGS := -eu` a failed init leaves
# a 0-byte baseline that still passes the `test -f` guard in security-secrets.
# `--baseline` also self-excludes the tracked baseline from its own scan and
# preserves audit annotations, neither of which a bare `scan` does.
.PHONY: security-secrets-init
security-secrets-init: ## Bootstrap/regenerate .secrets.baseline (review the diff before committing)
	@test -f .secrets.baseline || { uv run --group dev detect-secrets scan > .secrets.baseline.tmp && mv .secrets.baseline.tmp .secrets.baseline; }
	@uv run --group dev detect-secrets scan --baseline .secrets.baseline

.PHONY: security-updates
security-updates: ## Report only: CVE scan + outdated packages (writes nothing)
	@echo "=== CVE + adverse-status scan ==="
	@uv audit
	@echo "=== Outdated packages ==="
	@uv pip list --outdated

# NOTE the singular name. `security-update` (this target) REWRITES uv.lock;
# `security-updates` above only reports. The two names are one character
# apart because two claude-meta guides name them independently --
# best-practices/PYTHON.md section 15 for this one, SECURITY.md section 6b
# for the report. Recovering from the wrong one is `git checkout uv.lock`.
# Run `make test` after this target, not before committing the lock blind.
.PHONY: security-update
security-update: ## Rewrite uv.lock with upgraded deps, then re-sync (run make test after)
	@echo "=== Upgrading the lockfile (this WRITES uv.lock) ==="
	@uv lock --upgrade
	@uv sync --group dev

# =============================================================================
# Code Analysis
# =============================================================================

.PHONY: pysmelly
pysmelly: ## Run pysmelly code smell analysis
	@uvx pysmelly .

# On-demand only, never in `make check`: vulture exits 3 whenever it reports
# anything at all, so false positives re-emerge as Protocols grow methods.
# The --ignore-names list is the canonical one from claude-meta
# best-practices/PYTHON.md section 14 -- the Python protocol surface vulture
# is 100%-confident and wrong about. tests/ is excluded: @patch binder names
# are unused by construction.
.PHONY: vulture
vulture: ## Dead-code scan (on-demand; exits 3 on any finding)
	@uv run vulture $(filter-out tests,$(PY_SOURCES)) --min-confidence 90 \
		--ignore-names exc_type,exc_val,exc_tb,tb,whence

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

# The three fileplan state directories are excluded: mdformat escapes
# Markdown punctuation inside an item's `+++` TOML head (a trailing `_`
# becomes `\_`), which corrupts the head so fileplan refuses the file.
# .claude/skills is excluded for the same reason: mdformat rewrites a
# SKILL.md's YAML frontmatter into a horizontal rule.
.PHONY: format-docs
format-docs: ## Format markdown files
	@command -v mdformat >/dev/null 2>&1 || { echo "Error: mdformat not found. Install with: uv tool install mdformat --with mdformat-gfm"; exit 1; }
	@git ls-files -coz --exclude-standard '*.md' ':!docs/someday-maybe' ':!docs/plan' ':!docs/plan-archive' ':!.claude/skills' | xargs -0 mdformat

.PHONY: format-docs-check
format-docs-check: ## Check markdown formatting without modifying
	@command -v mdformat >/dev/null 2>&1 || { echo "Error: mdformat not found. Install with: uv tool install mdformat --with mdformat-gfm"; exit 1; }
	@git ls-files -coz --exclude-standard '*.md' ':!docs/someday-maybe' ':!docs/plan' ':!docs/plan-archive' ':!.claude/skills' | xargs -0 mdformat --check
