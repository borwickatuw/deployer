# deployer Makefile
#
# Usage: make [target]
# Run `make` or `make help` to see available targets.

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
security: security-bandit security-deps ## Run all security checks
	@echo ""
	@echo "=== Security Checks Complete ==="

.PHONY: security-bandit
security-bandit: ## Run bandit Python security linter
	@echo "=== Bandit Security Linter ==="
	@uv run bandit -c pyproject.toml -r bin src -ll

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
