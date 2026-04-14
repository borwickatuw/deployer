"""Audit logic for deploy.toml vs docker-compose.yml comparison."""

from pathlib import Path
from typing import Any

from ..config import (
    AuditConfig,
    ImageConfig,
    get_compose_services,
    parse_deploy_config,
    parse_docker_compose,
)
from ..utils import Colors, log_info, log_ok, log_section, log_warning

# Default infrastructure services to always ignore
DEFAULT_IGNORE_SERVICES = {
    "postgres",
    "redis",
    "mysql",
    "mongo",
    "mongodb",
    "memcached",
}


def audit_services(
    compose_services: dict[str, dict],
    deploy_services: dict[str, Any],
    audit_config: AuditConfig,
) -> list[str]:
    """Audit services and return list of issues.

    Args:
        compose_services: Services extracted from docker-compose.yml.
        deploy_services: Services from deploy.toml (dict of ServiceConfig or dict).
        audit_config: Audit configuration from deploy.toml.

    Returns:
        List of issue strings describing mismatches.
    """
    issues = []
    ignore = DEFAULT_IGNORE_SERVICES | audit_config.ignore_services
    mapping = audit_config.service_mapping

    # Build reverse mapping for lookup
    reverse_mapping = {v: k for k, v in mapping.items()}

    # Get service names from deploy_services
    deploy_service_names = set(deploy_services.keys())

    for name, config in compose_services.items():
        # Skip non-build services (they use pre-built images)
        if not config["has_build"]:
            continue

        # Skip ignored services
        if name in ignore:
            continue

        # Skip services with profiles (usually dev-only tools)
        if config["profiles"]:
            continue

        # Check if service exists in deploy.toml (directly or via mapping)
        mapped_name = mapping.get(name, name)
        if mapped_name not in deploy_service_names:
            issues.append(f"Service '{name}' in docker-compose not found in deploy.toml")
            if name != mapped_name:
                issues[-1] += f" (checked as '{mapped_name}')"

    # Check for services in deploy.toml that don't exist in docker-compose
    for name in deploy_service_names:
        original_name = reverse_mapping.get(name, name)
        if original_name not in compose_services and name not in compose_services:
            issues.append(f"Service '{name}' in deploy.toml not found in docker-compose")

    return issues


def audit_images(
    compose_services: dict[str, dict],
    deploy_images: dict[str, ImageConfig],
    audit_config: AuditConfig,
) -> list[str]:
    """Audit images/build contexts and return list of issues.

    Args:
        compose_services: Services extracted from docker-compose.yml.
        deploy_images: Images from deploy.toml (dict of ImageConfig).
        audit_config: Audit configuration from deploy.toml.

    Returns:
        List of issue strings describing missing images.
    """
    issues = []
    ignore = DEFAULT_IGNORE_SERVICES | audit_config.ignore_services | audit_config.ignore_images

    # Get all build contexts from docker-compose
    compose_contexts = {}
    for name, config in compose_services.items():
        if name in ignore:
            continue
        if config["profiles"]:
            continue
        if config["build_context"]:
            # Normalize context path (remove leading ./)
            context = config["build_context"].lstrip("./")
            compose_contexts[context] = name

    # Get all contexts from deploy.toml images (normalize the same way as compose)
    deploy_contexts = {}
    for name, img in deploy_images.items():
        context = img.context
        deploy_contexts[context.lstrip("./")] = name

    # Check for missing contexts
    for context, service_name in compose_contexts.items():
        if context not in deploy_contexts:
            issues.append(
                f"Build context '{context}' (from service '{service_name}') "
                f"not found in deploy.toml [images]"
            )

    return issues


def audit_env_vars(
    compose_services: dict[str, dict],
    deploy_env_vars: set[str],
    audit_config: AuditConfig,
) -> list[str]:
    """Audit environment variables and return list of issues.

    Args:
        compose_services: Services extracted from docker-compose.yml.
        deploy_env_vars: Environment variable names from deploy.toml.
        audit_config: Audit configuration from deploy.toml.

    Returns:
        List of issue strings describing missing env vars.
    """
    issues = []
    ignore_vars = audit_config.ignore_env_vars
    ignore_services = DEFAULT_IGNORE_SERVICES | audit_config.ignore_services

    # Common dev-only or infrastructure env vars to ignore by default.
    # On ECS Fargate, AWS credentials are provided by the task role via
    # the container metadata endpoint — no env vars needed. These only
    # appear in docker-compose for local S3-compatible services (MinIO).
    default_ignore = {
        "DEBUG",  # Usually different in dev vs prod
        "UV_LINK_MODE",  # uv-specific dev setting
        "PYTHONUNBUFFERED",  # Dev convenience
        "JAVA_OPTS",  # JVM tuning, usually different per env
        "AWS_ACCESS_KEY_ID",  # Local dev only; ECS uses task role
        "AWS_SECRET_ACCESS_KEY",  # Local dev only; ECS uses task role
        "AWS_S3_ENDPOINT_URL",  # MinIO/localstack endpoint for local S3 dev
    }
    ignore_vars = ignore_vars | default_ignore

    # Collect all env vars from docker-compose services with builds
    compose_env_vars = set()
    for name, config in compose_services.items():
        if name in ignore_services:
            continue
        if config["profiles"]:
            continue
        if config["has_build"]:
            compose_env_vars.update(config["environment"])

    # Find env vars in docker-compose but not in deploy.toml
    missing = compose_env_vars - deploy_env_vars - ignore_vars

    for var in sorted(missing):
        issues.append(f"Environment variable '{var}' in docker-compose not in deploy.toml")

    return issues


def run_audit(  # noqa: C901 — deploy.toml vs docker-compose audit with multiple checks
    project_dir: Path,
    compose_filename: str = "docker-compose.yml",
    deploy_filename: str = "deploy.toml",
    verbose: bool = True,
) -> tuple[int, list[str]]:
    """Run the audit and return (issue_count, list_of_issues).

    Args:
        project_dir: Path to project directory.
        compose_filename: Name of docker-compose file.
        deploy_filename: Name of deploy.toml file.
        verbose: Whether to print output.

    Returns:
        Tuple of (total_issues, list_of_issue_strings).
        Returns (-1, [error_message]) if files not found.
    """
    project_dir = Path(project_dir).resolve()
    compose_path = project_dir / compose_filename
    deploy_path = project_dir / deploy_filename

    all_issues = []

    # Check files exist
    if not compose_path.exists():
        return (-1, [f"docker-compose file not found: {compose_path}"])
    if not deploy_path.exists():
        return (-1, [f"deploy.toml not found: {deploy_path}"])

    if verbose:
        print(f"Auditing {Colors.CYAN}{project_dir.name}{Colors.NC}")
        print(f"  docker-compose: {compose_path.name}")
        print(f"  deploy.toml: {deploy_path.name}")

    # Parse files
    compose = parse_docker_compose(compose_path)
    deploy = parse_deploy_config(deploy_path)

    # Extract data
    compose_services = get_compose_services(compose)
    deploy_services = deploy.services
    deploy_images = deploy.images
    deploy_env_vars = deploy.get_all_env_var_names()
    audit_config = deploy.audit

    # Show audit config if present
    if verbose and (
        audit_config.ignore_services
        or audit_config.service_mapping
        or audit_config.ignore_env_vars
        or audit_config.ignore_images
    ):
        log_section("Audit Configuration")
        if audit_config.ignore_services:
            log_info(f"Ignoring services: {', '.join(sorted(audit_config.ignore_services))}")
        if audit_config.service_mapping:
            mappings = [f"{k}→{v}" for k, v in audit_config.service_mapping.items()]
            log_info(f"Service mappings: {', '.join(mappings)}")
        if audit_config.ignore_env_vars:
            log_info(f"Ignoring env vars: {', '.join(sorted(audit_config.ignore_env_vars))}")

    total_issues = 0

    # Audit services
    if verbose:
        log_section("Services")
    service_issues = audit_services(compose_services, deploy_services, audit_config)
    if service_issues:
        if verbose:
            for issue in service_issues:
                log_warning(issue)
        all_issues.extend(service_issues)
        total_issues += len(service_issues)
    elif verbose:
        log_ok("All services accounted for")

    # Audit images
    if verbose:
        log_section("Images")
    image_issues = audit_images(compose_services, deploy_images, audit_config)
    if image_issues:
        if verbose:
            for issue in image_issues:
                log_warning(issue)
        all_issues.extend(image_issues)
        total_issues += len(image_issues)
    elif verbose:
        log_ok("All build contexts accounted for")

    # Audit environment variables
    if verbose:
        log_section("Environment Variables")
    env_issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
    if env_issues:
        if verbose:
            for issue in env_issues:
                log_warning(issue)
        all_issues.extend(env_issues)
        total_issues += len(env_issues)
    elif verbose:
        log_ok("All environment variables accounted for")

    # Summary
    if verbose:
        log_section("Summary")
        if total_issues == 0:
            print(f"  {Colors.GREEN}No issues found!{Colors.NC}")
        else:
            print(f"  {Colors.YELLOW}{total_issues} issue(s) found{Colors.NC}")
            print("\n  To acknowledge intentional differences, add an [audit] section")
            print("  to deploy.toml. Run with --help or see script docstring for examples.")

    return (total_issues, all_issues)
