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
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-25s %s\n", $$1, $$2}'

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

.PHONY: tofu-fmt
tofu-fmt: ## Check OpenTofu formatting (fix with: tofu fmt -recursive .)
	@echo "=== Checking OpenTofu Formatting ==="
	@command -v tofu >/dev/null 2>&1 || { \
		echo "tofu not found on PATH -- see docs/GETTING-STARTED.md"; exit 1; }
	@tofu fmt -check -recursive . || { \
		echo "Unformatted. Fix with: tofu fmt -recursive ."; exit 1; }

# Not in `make check`: every directory needs `tofu init` first, which reaches
# the provider registry over the network. Run it after editing any .tf file.
# It is what catches a module using a provider argument the version floor in
# versions.tf cannot supply.
.PHONY: tofu-validate
tofu-validate: ## Init (no backend) and validate the root module, environments/ and every module
	@echo "=== OpenTofu Validate ==="
	@for d in . environments modules/*/; do \
		[ -n "$$(ls $$d/*.tf 2>/dev/null)" ] || continue; \
		echo "--- $$d"; \
		( cd $$d && tofu init -backend=false -input=false >/dev/null \
			&& tofu validate -no-color ); \
	done

.PHONY: lint
lint: ## Check formatting (black, isort, tofu fmt) and lint (ruff, pyright)
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
	@echo ""
	@$(MAKE) --no-print-directory tofu-fmt

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

# Not part of `check`: pa11y arrives through npx, so this target needs node and
# the network. The surface it covers is the CloudFront 503 page in
# modules/cloudfront-alb/locals.tf — the only HTML this repo emits.
.PHONY: a11y
a11y: ## Run pa11y (WCAG 2.1 AA) against the rendered CloudFront error page
	@echo "=== Accessibility (pa11y) ==="
	@uv run bin/a11y.py

# MAKEFILE.md Practice #15: re-parse every structured file the repo owns, so
# a corruption fails `check` and names the file, whatever caused it. One
# parser pass over every tracked TOML and JSON file (the baseline is JSON);
# exits at the first bad file with its path and the parser's own error.
define PARSE_STRUCTURED
import json, sys, tomllib
for path in filter(None, sys.stdin.read().split("\0")):
    try:
        with open(path, "rb") as f:
            (tomllib.load if path.endswith(".toml") else json.load)(f)
    except ValueError as e:
        sys.exit(f"Error: {path} does not parse: {e}")
endef
export PARSE_STRUCTURED

.PHONY: check-structured
check-structured: ## Re-parse every tracked TOML/JSON file, every fileplan item, and the sample compose fixture
	@uv run --group dev fileplan list --json >/dev/null
	@git ls-files -z -- '*.toml' '*.json' .secrets.baseline | uv run python -c "$$PARSE_STRUCTURED"
	@docker compose -f tests/fixtures/sample_docker_compose.yml config -q --no-interpolate || { echo "Error: tests/fixtures/sample_docker_compose.yml does not parse"; exit 1; }

.PHONY: check
check: check-structured lint test format-docs-check security-secrets ## Run structured-file/lint/test/docs/secrets checks
	@echo ""
	@echo "=== All Checks Passed ==="

# =============================================================================
# Security
# =============================================================================

.PHONY: security
security: security-bandit security-deps security-lockfiles security-secrets security-checkov ## Run all security checks
	@echo ""
	@echo "=== Security Checks Complete ==="

.PHONY: security-bandit
security-bandit: ## Run bandit Python security linter
	@echo "=== Bandit Security Linter ==="
	@uv run bandit -c pyproject.toml -r $(filter-out tests,$(PY_SOURCES)) -ll

# Scan the whole tree, not just modules/: the root module (main.tf,
# variables.tf, outputs.tf) and environments/deployer.tf are symlinked into
# deployer-environments and are live infrastructure too. The skip list with
# its rationale and the scanner pin live in bin/checkov-scan.sh, which
# deployer-environments runs too -- one list, one pin, for both repos.
.PHONY: security-checkov
security-checkov: ## Run Checkov IaC scanner on all OpenTofu code
	@bin/checkov-scan.sh .

.PHONY: security-deps
security-deps: ## Check dependency vulnerabilities
	@echo "=== Dependency Vulnerability Scan (uv audit) ==="
	@uv audit

# SECURITY.md Practice #6: `uv audit` reads the root lockfile only. Every
# lockfile git tracks below the root is declared here, space-separated;
# bin/security-lockfiles (claude-meta's copy, verbatim) fails on an
# undeclared or vanished one and audits each declared one by its ecosystem.
NESTED_LOCKFILES := modules/db-on-shared-rds/lambda/requirements.txt modules/db-users/lambda/requirements.txt

.PHONY: security-lockfiles
security-lockfiles: ## Fail on an undeclared or vanished nested lockfile; audit each declared one
	@# 3.12 is the Lambda runtime both db-*/main.tf declare: keep the three in step.
	@bin/security-lockfiles --python-version 3.12 $(NESTED_LOCKFILES)

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

.PHONY: security-report
security-report: ## Report only: CVE scan + outdated packages (writes nothing)
	@echo "=== CVE + adverse-status scan ==="
	@uv audit
	@echo "=== Outdated packages ==="
	@uv pip list --outdated

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

# The fileplan state directories declared in plan.toml are excluded:
# mdformat escapes Markdown punctuation inside an item's `+++` TOML head (a
# trailing `_` becomes `\_`), which corrupts the head so fileplan refuses the
# file. `:!docs/plan-archive` is a directory pathspec, so it takes the whole
# tree -- `items/` plus the register (PLAN-ARCHIVE.md) and its rotated
# segments -- out of scope too: those are frozen historical records (DOCS
# Practice 15) and mdformat rewrites them (FILEPLAN Practice 4). Narrowing
# this to `docs/plan-archive/items` so the registers stay formatted is the
# mistake FILEPLAN P4 warns against; don't reintroduce it. The host mdformat
# carries mdformat-frontmatter, so .claude/skills' SKILL.md YAML frontmatter
# and any docs/drafts front matter round-trip intact -- no pathspec exclusion
# needed for those (FILEPLAN Practice 4).
.PHONY: format-docs
format-docs: ## Format markdown files
	@command -v mdformat >/dev/null 2>&1 || { echo "Error: mdformat not found. Install with: uv tool install mdformat --with mdformat-gfm --with mdformat-frontmatter"; exit 1; }
	@git ls-files -coz --exclude-standard '*.md' ':!docs/someday-maybe' ':!docs/plan' ':!docs/plan-archive' | xargs -0 mdformat

.PHONY: format-docs-check
format-docs-check: ## Check markdown formatting without modifying
	@command -v mdformat >/dev/null 2>&1 || { echo "Error: mdformat not found. Install with: uv tool install mdformat --with mdformat-gfm --with mdformat-frontmatter"; exit 1; }
	@git ls-files -coz --exclude-standard '*.md' ':!docs/someday-maybe' ':!docs/plan' ':!docs/plan-archive' | xargs -0 mdformat --check
