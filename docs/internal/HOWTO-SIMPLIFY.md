# How to Simplify the Deployer Codebase

This guide provides a repeatable methodology for identifying and implementing simplification opportunities in the Deployer codebase. Use this document when you want to reduce complexity, improve maintainability, or consolidate duplicate code or documentation.

## When to Use This Guide

- During dedicated refactoring sessions
- When a file becomes difficult to navigate (300+ lines for Python, 200+ for Terraform modules)
- When you notice repeated patterns across multiple files
- Before adding new features to overly complex areas
- During code review when complexity is flagged
- When documentation has grown unwieldy or duplicative
- When CLAUDE.md exceeds 100 lines

**Quality-first principle**: Simplification must not introduce bugs. Every change should maintain or improve test coverage, and all tests must pass before and after changes.

## Pre-Simplification Checklist

Before starting any simplification work:

- [ ] **Run tests**: Verify all tests pass (`uv run pytest`)
- [ ] **Create a branch**: `git checkout -b simplify/<area>` (e.g., `simplify/deploy-script`)
- [ ] **Identify scope**: Document what files/modules you plan to modify
- [ ] **Check dependencies**: Note what other code depends on the areas you're changing
- [ ] **Commit working state**: Ensure you have a clean commit to revert to

## Finding Simplification Opportunities

### Large File Analysis

Files over these thresholds often benefit from being split into focused modules:

- **Python scripts**: 300 lines
- **Python library modules**: 250 lines
- **Terraform modules**: 200 lines
- **Test files**: 400 lines

**How to identify large files:**

```bash
# Find Python files over 300 lines
find . -name "*.py" ! -path "./.venv/*" -exec wc -l {} + | sort -rn | head -20

# Find Terraform files over 200 lines
find . -name "*.tf" ! -path "./.terraform/*" -exec wc -l {} + | sort -rn | head -20
```

**Criteria for splitting files:**

1. File has multiple distinct responsibilities (e.g., different AWS services)
1. File has groups of related functions that could be isolated
1. File has utility functions mixed with core business logic
1. File is difficult to navigate when looking for specific functionality

**Questions to ask:**

- Can I describe what this file does in one sentence?
- Are there natural groupings of functions/classes?
- Would a new developer know where to add new code?

### Code Duplication Detection

**Patterns to search for in Python:**

```bash
# Find similar function definitions
grep -rn "def get_" scripts src --include="*.py"

# Find repeated AWS client patterns
grep -rn "boto3.client" src --include="*.py"

# Find similar validation patterns
grep -rn "if not " scripts src --include="*.py"
```

**Patterns to search for in Terraform:**

```bash
# Find similar resource definitions
grep -rn "resource \"aws_" modules --include="*.tf"

# Find repeated variable patterns
grep -rn "variable \"" modules --include="*.tf"

# Find similar data sources
grep -rn "data \"aws_" modules --include="*.tf"
```

**Signs of duplication:**

- Functions with nearly identical signatures in different files
- Copy-pasted code blocks with minor variations
- Similar AWS API call patterns
- Repeated Terraform resource configurations

### Import Analysis (Python)

**Check for unused imports:**

```bash
# Use ruff to check for unused imports
uv run ruff check --select F401 src scripts
```

**Identify inline imports:**

```bash
# Find imports inside functions (may indicate circular import workarounds)
grep -rn "^    import \|^    from " src scripts --include="*.py"
```

Inline imports sometimes indicate:

- Circular dependency issues that need restructuring
- Lazy loading that could be simplified
- Import organization problems

### Code Complexity and Dead Code Analysis

**Install analysis tools:**

```bash
uv add --dev vulture radon
```

**Find dead code (unused functions, classes, variables):**

```bash
# High-confidence dead code (85%+ confidence)
uv run vulture bin/ src/ --min-confidence 85

# Include tests in analysis
uv run vulture bin/ src/ tests/ --min-confidence 80
```

**Analyze cyclomatic complexity:**

```bash
# Find functions with high complexity (grades D-F need attention)
uv run radon cc bin/ src/ -a -s

# Show only problematic functions (complexity > 10)
uv run radon cc bin/ src/ -a -s -nc
```

Complexity grades: A (1-5), B (6-10), C (11-20), D (21-30), F (>30)

**Check maintainability index:**

```bash
# Show maintainability scores (A is best, F needs work)
uv run radon mi bin/ src/ -s

# Show only files needing attention (below A grade)
uv run radon mi bin/ src/ -s | grep -v " A "
```

Maintainability grades: A (100-20), B (19-10), C (9-0)

**Find unused imports/variables with ruff:**

```bash
uv run ruff check bin/ src/ --select=F401,F841,C901
```

- F401: unused imports
- F841: unused variables
- C901: high cyclomatic complexity

### Terraform Module Analysis

**Check for unused variables:**

```bash
# List all declared variables
grep -rn "^variable " modules --include="*.tf" | cut -d'"' -f2 | sort

# Cross-reference with actual usage
grep -rh "var\." modules --include="*.tf" | grep -oE "var\.[a-z_]+" | sort -u
```

**Find hardcoded values that should be variables:**

```bash
# Find hardcoded AWS regions
grep -rn "us-west-2\|us-east-1" modules --include="*.tf"

# Find hardcoded instance types
grep -rn "t3\.\|t4g\." modules --include="*.tf"
```

## Refactoring Patterns

### Extracting Modules from Large Python Files

**Step-by-step process:**

1. **Identify the grouping**: Determine which functions/classes belong together
1. **Create the new module**: Create a new file in the appropriate package
1. **Move code**: Cut and paste the code, updating imports
1. **Add re-exports** (if needed for backwards compatibility):
   ```python
   # In original file, temporarily:
   from .new_module import function_name  # noqa: F401
   ```
1. **Update imports**: Find and update all imports across the codebase
1. **Remove re-exports**: Once all imports are updated, remove backwards compatibility
1. **Run tests**: Verify everything still works

**Example - splitting a large script:**

```
# Before:
bin/deploy.py (800+ lines)
  - Config loading functions
  - Docker build functions
  - ECR push functions
  - ECS deployment functions
  - Health check functions

# After:
src/deployer/deploy/config.py      # Config parsing
src/deployer/deploy/docker.py      # Docker build/tag
src/deployer/deploy/ecr.py         # ECR push operations
src/deployer/deploy/ecs.py         # ECS deployment
src/deployer/deploy/health.py      # Health checking
bin/deploy.py                  # Thin CLI wrapper
```

### Extracting Terraform Submodules

**When to extract a submodule:**

- A resource group is reused across environments with similar configuration
- A logical component (e.g., security groups, IAM roles) grows complex
- The main.tf exceeds 200 lines

**Example - extracting security group module:**

```
# Before:
modules/ecs-service/main.tf (225 lines)
  - Task definition
  - Service definition
  - Security groups
  - IAM roles
  - CloudWatch logs

# After:
modules/ecs-service/main.tf          # Task and service only
modules/ecs-service/security.tf      # Security groups
modules/ecs-service/iam.tf           # IAM roles and policies
modules/ecs-service/logging.tf       # CloudWatch configuration
```

### Consolidating Duplicate Code

**When to abstract:**

- Same code appears 3+ times
- The duplicated logic has a clear, single purpose
- Changes to the logic would require updates in multiple places

**When NOT to abstract:**

- Only 2 occurrences and they might diverge
- The "duplicate" code has different contexts
- Abstraction would require many parameters/options
- The duplication is coincidental, not conceptual

**Example - AWS client consolidation:**

```python
# Before: duplicated in multiple modules
def get_ecs_client():
    return boto3.client("ecs", region_name="us-west-2")

def get_rds_client():
    return boto3.client("rds", region_name="us-west-2")

# After: utility function
# In src/deployer/aws/clients.py
def get_client(service: str, region: str | None = None) -> boto3.client:
    """Get a boto3 client for the specified service."""
    return boto3.client(service, region_name=region or get_default_region())
```

### Simplifying Complex Functions

**Signs a function needs simplification:**

- More than 50 lines
- Deep nesting (3+ levels of indentation)
- Multiple unrelated responsibilities
- Difficult to write tests for
- Many local variables

**Techniques:**

1. **Extract helper methods**: Move logical chunks into well-named private functions
1. **Use early returns**: Reduce nesting by handling edge cases first
1. **Split into smaller functions**: Each doing one thing well
1. **Use dataclasses for config**: Replace dictionaries with typed structures

**Example - before:**

```python
def deploy_service(config, environment):
    if config.get("migrations", {}).get("enabled"):
        # 30 lines of migration logic
        ...
    if config.get("services"):
        for service in config["services"]:
            # 40 lines of service deployment
            ...
    if config.get("health_check"):
        # 25 lines of health checking
        ...
```

**Example - after:**

```python
def deploy_service(config: DeployConfig, environment: str) -> DeployResult:
    if config.migrations.enabled:
        _run_migrations(config)

    for service in config.services:
        _deploy_ecs_service(service, environment)

    if config.health_check:
        _wait_for_healthy(config.health_check)
```

## Quality Safeguards

### During Refactoring

- [ ] **Run tests after each change**: Don't batch multiple changes
- [ ] **Keep commits small and focused**: One logical change per commit
- [ ] **Commit messages should explain why**: Not just what
- [ ] **Review import changes carefully**: Easy to break things

### After Refactoring

- [ ] **Run full test suite**: `uv run pytest`
- [ ] **Run type checking**: `uv run mypy src scripts` (if configured)
- [ ] **Run linting**: `uv run ruff check src scripts`
- [ ] **Validate Terraform**: `tofu validate` in each module
- [ ] **Test manually**: Run a dry-run deployment

### Common Pitfalls

- **Breaking circular imports**: Moving code can create new circular dependencies
- **Changing public APIs**: External code may depend on import paths
- **Over-abstracting**: Don't create abstractions for their own sake
- **Losing context**: Comments and docstrings should move with code
- **Terraform state drift**: Module restructuring may require state moves

## Documentation Simplification

The same principles that apply to code apply to documentation. This section provides guidance for reviewing and simplifying docs.

### When to Simplify Documentation

- A document exceeds 400 lines and covers multiple unrelated topics
- The same information appears in multiple documents
- You find yourself searching multiple files for related information
- A document has grown organically and lost its original focus
- CLAUDE.md has accumulated guidance that belongs elsewhere

### Analyzing Documentation

**Find large documentation files:**

```bash
# Count lines in all markdown files
wc -l CLAUDE.md docs/*.md | sort -rn
```

**Thresholds to consider:**

| File Type      | Threshold | Action                             |
| -------------- | --------- | ---------------------------------- |
| CLAUDE.md      | 100 lines | Keep slim; move details to docs/   |
| HOWTO guides   | 400 lines | Consider splitting by topic        |
| Reference docs | 600 lines | Consider splitting by section      |
| DECISIONS.md   | No limit  | Chronological log, grows naturally |

### CLAUDE.md Considerations

CLAUDE.md is read on every Claude Code conversation, so token cost scales with file size. Keep it focused on:

- **Essential coding standards** that apply to every task
- **Pointers to other docs** rather than duplicating their content
- **Short, actionable rules** rather than detailed explanations

**Move to other docs:**

- Detailed feature requirements -> specific HOWTO guides
- Implementation specifics -> `docs/DESIGN.md`
- Architectural context -> `docs/DECISIONS.md`
- Checklists and validation -> `docs/DEPLOYMENT-GUIDE.md` (Checklists section)

### Finding Documentation Duplication

**Patterns to search for:**

```bash
# Find similar section headers across docs
grep -rn "^##" docs/*.md CLAUDE.md | cut -d: -f3 | sort | uniq -c | sort -rn

# Find repeated phrases
grep -rn "environment variable" docs/*.md CLAUDE.md
grep -rn "tofu apply" docs/*.md CLAUDE.md

# Find documents covering similar topics
grep -l "Cognito" docs/*.md
grep -l "staging" docs/*.md
```

### Documentation Cross-References

Good documentation uses clear cross-references instead of duplication:

```markdown
# Good: Clear cross-reference
For Cognito user management details, see [STAGING-ENVIRONMENTS.md](STAGING-ENVIRONMENTS.md).

# Bad: Partial duplication
For Cognito user management, see STAGING-ENVIRONMENTS.md. The main commands are:
- cognito.py create
- cognito.py list
[...repeating what's in the other doc...]
```

## Verification

### Metrics to Track

Before and after simplification, record:

```bash
# Total lines of Python code
find . -name "*.py" ! -path "./.venv/*" -exec wc -l {} + | tail -1

# Lines in target files
wc -l bin/deploy.py src/deployer/core/*.py

# Total lines of Terraform
find . -name "*.tf" ! -path "./.terraform/*" -exec wc -l {} + | tail -1

# Number of files
find . -name "*.py" ! -path "./.venv/*" | wc -l
find . -name "*.tf" ! -path "./.terraform/*" | wc -l
```

### Success Criteria

A successful simplification:

- [ ] All tests pass
- [ ] No new test failures or skips
- [ ] Target files are under threshold
- [ ] Code is easier to navigate and understand
- [ ] No functionality was lost or changed
- [ ] Terraform validates in all modules

## Current Opportunities

These are known areas that could benefit from simplification:

### Code - Potential Opportunities

| File                       | Lines | Notes                                         |
| -------------------------- | ----- | --------------------------------------------- |
| `bin/deploy.py`            | 827   | Could extract Docker/ECR/ECS logic to library |
| `tests/unit/test_audit.py` | 452   | Large but may be appropriate for coverage     |
| `bin/cognito.py`           | 446   | Could share patterns with environment.py      |

### Terraform - Potential Opportunities

| Module                              | Lines | Notes                                          |
| ----------------------------------- | ----- | ---------------------------------------------- |
| `modules/alb/main.tf`               | 259   | Could split listeners, security, target groups |
| `modules/staging-scheduler/main.tf` | 229   | Lambda + EventBridge could be separated        |
| `modules/ecs-service/main.tf`       | 225   | Could extract IAM, security groups             |

### Documentation - Current State

| Document                       | Lines | Status                            |
| ------------------------------ | ----- | --------------------------------- |
| `docs/CONFIG-REFERENCE.md`     | 576   | Reference doc, may be appropriate |
| `docs/ARCHITECTURE.md`         | 339   | Moderate, acceptable              |
| `docs/STAGING-ENVIRONMENTS.md` | ~200  | Merged from ACCESS + SCHEDULING   |
| `CLAUDE.md`                    | 59    | Well under threshold              |

## Quick Reference

### Code Commands

```bash
# Find large Python files
find . -name "*.py" ! -path "./.venv/*" -exec wc -l {} + | sort -rn | head -20

# Find large Terraform files
find . -name "*.tf" ! -path "./.terraform/*" -exec wc -l {} + | sort -rn | head -20

# Search for duplicate patterns
grep -rn "PATTERN" scripts src --include="*.py"

# Run tests
uv run pytest

# Run linting
uv run ruff check src scripts

# Validate Terraform
tofu validate
```

### Code Analysis Commands

```bash
# Dead code detection
uv run vulture bin/ src/ --min-confidence 85

# Cyclomatic complexity (find complex functions)
uv run radon cc bin/ src/ -a -s

# Maintainability index
uv run radon mi bin/ src/ -s

# Unused imports/variables (via ruff)
uv run ruff check bin/ src/ --select=F401,F841,C901
```

### Documentation Commands

```bash
# Find large documentation files
wc -l CLAUDE.md docs/*.md | sort -rn

# Find similar section headers (potential duplication)
grep -rn "^##" docs/*.md CLAUDE.md | cut -d: -f3 | sort | uniq -c | sort -rn

# Find documents covering a topic
grep -l "cognito" docs/*.md
grep -l "staging" docs/*.md
```

### Commit Message Templates

**Code refactoring:**

```
Refactor: Extract <module> from <file>

- Move <functions/classes> to <new location>
- Update imports in <affected files>
- No functional changes

Part of codebase simplification effort.
```

**Terraform refactoring:**

```
Refactor: Split <module> into submodules

- Extract <resources> to <new file>
- No resource changes, output-compatible

Part of infrastructure simplification effort.
```

**Documentation simplification:**

```
Docs: Reorganize <topic> documentation

- Split <large-doc> into focused files
- Remove duplicated content from <file>
- Add cross-references between related docs
```
