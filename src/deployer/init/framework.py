"""Framework detection utilities."""

# Framework detection patterns based on environment variables
FRAMEWORK_ENV_PATTERNS = {
    "django": ["DJANGO_SETTINGS_MODULE", "DJANGO_SECRET_KEY"],
    "rails": ["RAILS_ENV", "RAILS_MASTER_KEY", "RAILS_LOG_TO_STDOUT"],
    "flask": ["FLASK_APP", "FLASK_ENV"],
    "fastapi": ["FASTAPI_ENV"],
    "express": ["NODE_ENV", "EXPRESS_PORT"],
    "nextjs": ["NEXT_PUBLIC_", "NEXTAUTH_"],
}

# Framework detection patterns based on Dockerfile content
FRAMEWORK_DOCKERFILE_PATTERNS = {
    "django": ["manage.py", "wsgi", "gunicorn", "django"],
    "rails": ["rails", "puma", "bundle exec", "rake"],
    "flask": ["flask", "gunicorn"],
    "fastapi": ["uvicorn", "fastapi"],
    "express": ["npm start", "node ", "express"],
    "nextjs": ["next start", "next build"],
}

# Migration commands by framework
MIGRATION_COMMANDS = {
    "django": ["python", "manage.py", "migrate"],
    "rails": ["bundle", "exec", "rails", "db:migrate"],
    "flask": ["flask", "db", "upgrade"],
    "fastapi": ["alembic", "upgrade", "head"],
}

# Default ports by framework
DEFAULT_PORTS = {
    "django": 8000,
    "rails": 3000,
    "flask": 5000,
    "fastapi": 8000,
    "express": 3000,
    "nextjs": 3000,
}


def detect_framework(
    env_vars: list[str] | None,
    dockerfile_content: str | None,
) -> str | None:
    """Detect application framework from environment variables and Dockerfile.

    Args:
        env_vars: List of environment variable names.
        dockerfile_content: Content of the Dockerfile.

    Returns:
        Detected framework name, or None if unknown.
    """
    scores: dict[str, int] = {}

    # Check environment variables
    if env_vars:
        env_vars_upper = [v.upper() for v in env_vars]
        for framework, patterns in FRAMEWORK_ENV_PATTERNS.items():
            for pattern in patterns:
                # Check for exact match or prefix match (for NEXT_PUBLIC_ etc)
                if any(
                    env_var == pattern or (pattern.endswith("_") and env_var.startswith(pattern))
                    for env_var in env_vars_upper
                ):
                    scores[framework] = scores.get(framework, 0) + 2

    # Check Dockerfile content
    if dockerfile_content:
        content_lower = dockerfile_content.lower()
        for framework, patterns in FRAMEWORK_DOCKERFILE_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in content_lower:
                    scores[framework] = scores.get(framework, 0) + 1

    # Return the highest scoring framework
    if scores:
        return max(scores, key=scores.get)
    return None


def get_migration_command(framework: str | None) -> list[str] | None:
    """Get the migration command for a framework.

    Args:
        framework: Framework name (e.g., 'django', 'rails').

    Returns:
        List of command arguments, or None if no migration command known.
    """
    if framework:
        return MIGRATION_COMMANDS.get(framework)
    return None


def get_default_port(framework: str | None) -> int:
    """Get the default port for a framework.

    Args:
        framework: Framework name (e.g., 'django', 'rails').

    Returns:
        Default port number, or 8000 if framework is None.

    Raises:
        KeyError: If framework is not recognized.
    """
    if framework:
        return DEFAULT_PORTS[framework]
    return 8000
