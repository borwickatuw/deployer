"""Shared CLI utilities for bin/ scripts."""

import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .aws_profile import configure_aws_profile, configure_aws_profile_for_environment
from .environment import get_environment_path, validate_environment_deployed
from .links import get_linked_deploy_toml
from .logging import log, log_error, log_error_stderr


class EnvironmentConfigError(Exception):
    """Raised when environment path or config cannot be loaded."""


@dataclass(frozen=True)
class EnvironmentInfrastructure:
    """Infrastructure identifiers read from an environment's config.toml."""

    config: dict
    cluster_name: str | None
    rds_id: str | None


def prompt_or_exit(prompt: str) -> str:
    """Read a line from stdin, treating EOF/Ctrl-C as "Cancelled" and exiting.

    Args:
        prompt: Text to show before reading.

    Returns:
        The stripped input.

    Raises:
        SystemExit: With code 1 if the user cancels (EOF/Ctrl-C). Every bin/
            command wraps its body in sys.exit(cmd_...()), so this is the same
            exit status the old per-site "return 1" produced.
    """
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        log_error("Cancelled")
        raise SystemExit(1) from None


def confirm_action(skip: bool = False) -> bool:
    """Prompt for confirmation before a destructive action.

    Args:
        skip: If True, skip the prompt and return True (for --yes flag).

    Returns:
        True if confirmed, False if cancelled.
    """
    if skip:
        return True

    if prompt_or_exit("Continue? [y/N]: ").lower() not in ("y", "yes"):
        log_error("Cancelled")
        return False

    return True


@contextmanager
def exit_on(*excs: type[BaseException], prefix: str = "") -> Iterator[None]:
    """Turn the given exceptions into a clean "Error: ..." exit.

    Wrap the single call that can fail — not a whole command body — so an
    unrelated exception of the same type is not swallowed.

    Args:
        excs: Exception types to catch.
        prefix: Text prepended to the exception message.

    Raises:
        SystemExit: With code 1 when one of ``excs`` is raised.
    """
    try:
        yield
    except excs as e:
        log_error_stderr(f"{prefix}{e}")
        sys.exit(1)


def resolve_deploy_toml_or_exit(
    environment: str | None,
    deploy_toml: str | None,
    *,
    specify_hint: str,
    link_benefit: str,
) -> Path:
    """Resolve the deploy.toml to use, or exit 1 explaining how to link one.

    Precedence is explicit flag, then the link registry, then an error naming
    link-environments.py. When the explicit flag was used, a tip suggesting the
    link is printed so the flag can be dropped next time.

    Args:
        environment: Environment name whose link is looked up. May be None only
            when ``deploy_toml`` is given — there is then nothing to link, so
            the tip is skipped. Callers that allow this must reject
            "no environment and no flag" themselves, with their own usage error.
        deploy_toml: Explicit --deploy-toml value, or None.
        specify_hint: The "Or specify:" invocation for this script, e.g.
            "ssm-secrets.py check myapp-staging --deploy-toml /path/to/deploy.toml".
        link_benefit: What linking buys, completing "to ...", e.g.
            "check with just: ssm-secrets.py check myapp-staging".

    Returns:
        The resolved, existing deploy.toml path.

    Raises:
        SystemExit: With code 1 if no deploy.toml can be resolved or the
            resolved path is not a readable .toml file.
    """
    if deploy_toml:
        config_path = Path(deploy_toml).expanduser().resolve()
        if environment:
            print(f"Tip: Run 'python bin/link-environments.py {environment} {config_path}'")
            print(f"     to {link_benefit}\n")
    else:
        config_path = get_linked_deploy_toml(environment)
        if config_path is None:
            log_error_stderr(f"No deploy.toml linked for '{environment}'")
            print(
                f"\nTo link: python bin/link-environments.py {environment} /path/to/deploy.toml",
                file=sys.stderr,
            )
            print(f"Or specify: {specify_hint}", file=sys.stderr)
            sys.exit(1)
        log(f"Using linked deploy.toml: {config_path}")

    if config_path.is_dir():
        log_error_stderr(f"Config path is a directory, expected a .toml file: {config_path}")
        sys.exit(1)
    if not config_path.exists():
        log_error_stderr(f"Config file not found: {config_path}")
        sys.exit(1)
    if config_path.suffix != ".toml":
        log_error_stderr(f"Config file must be a .toml file, got: {config_path}")
        sys.exit(1)

    return config_path


def configure_profile_or_exit(operation: str, environment: str) -> None:
    """Configure and validate the AWS profile for an operation. Exits on error.

    Args:
        operation: Profile operation name (e.g. "deploy", "infra").
        environment: Environment name whose config.toml selects the profile.

    Raises:
        SystemExit: With code 1 if the profile is missing or invalid.
    """
    try:
        configure_aws_profile_for_environment(operation, environment, validate=True)
    except RuntimeError as e:
        log_error(str(e))
        sys.exit(1)


def configure_aws_for_operation(operation: str, environment: str | None) -> None:
    """Configure the AWS profile for an operation, with or without an environment.

    Args:
        operation: Profile operation name (e.g. "cognito", "secrets").
        environment: Environment name, or None to use the operation default.
    """
    if environment:
        configure_aws_profile_for_environment(operation, environment)
    else:
        configure_aws_profile(operation)


def validate_and_configure(environment: str) -> None:
    """Require a deployed environment and configure its infra AWS profile.

    Callers print their own banner before calling this.

    Args:
        environment: Environment name.

    Raises:
        SystemExit: With code 1 if the environment is not deployed.
    """
    _, error = validate_environment_deployed(environment)
    if error:
        log_error(error)
        sys.exit(1)

    configure_aws_profile_for_environment("infra", environment)
    print()


def load_environment_infrastructure(
    environment: str,
    require_cluster: bool = False,
    require_rds: bool = False,
) -> EnvironmentInfrastructure:
    """Load an environment's config.toml and read its infrastructure IDs.

    Deliberately logger-free so read-only tools can call it.

    Args:
        environment: Environment name.
        require_cluster: Exit if no ECS cluster name is configured.
        require_rds: Exit if no RDS instance ID is configured.

    Returns:
        The resolved config, cluster name and RDS instance ID.

    Raises:
        SystemExit: With code 1 if the config cannot be loaded or a required
            piece of infrastructure is missing.
    """
    from ..core.config import load_environment_config  # noqa: PLC0415 — avoids a utils/core cycle

    env_path = get_environment_path(environment)
    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        log_error(f"Failed to load config: {e}")
        raise SystemExit(1) from None

    infrastructure = config.get("infrastructure", {})
    cluster_name = infrastructure.get("cluster_name")
    rds_id = infrastructure.get("rds_instance_id")

    if require_cluster and not cluster_name:
        log_error("Unable to determine ECS cluster name")
        raise SystemExit(1)

    if require_rds and not rds_id:
        log_error("RDS instance not configured for this environment")
        raise SystemExit(1)

    return EnvironmentInfrastructure(config=config, cluster_name=cluster_name, rds_id=rds_id)


def iter_deployed_environments(
    env_names: Iterable[str], header_suffix: str = ""
) -> Iterator[tuple[str, Path]]:
    """Print a banner per environment and yield only the deployed ones.

    Environments whose directory is missing or that have no terraform state
    print a one-line reason and are skipped.

    Args:
        env_names: Environment names to walk.
        header_suffix: Text appended to the "Environment: <name>" header line.

    Yields:
        Tuples of (env_name, env_path) for deployed environments.
    """
    for env_name in env_names:
        env_path = get_environment_path(env_name)

        print(f"\n{'=' * 60}")
        print(f"Environment: {env_name}{header_suffix}")
        print(f"{'=' * 60}")

        if not env_path.exists():
            print("  Directory not found")
            continue

        if not (env_path / "terraform.tfstate").exists():
            print("  Status: Not deployed")
            continue

        yield env_name, env_path


def require_environment(env_name: str) -> tuple[Path, dict]:
    """Load and return the environment path and resolved config.

    Validates the environment exists and loads its config.toml.

    Args:
        env_name: Environment name (e.g., "myapp-staging").

    Returns:
        Tuple of (env_path, resolved_config).

    Raises:
        EnvironmentConfigError: If environment is invalid or config can't be loaded.
    """
    from ..core.config import load_environment_config  # noqa: PLC0415

    env_path = get_environment_path(env_name)
    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        raise EnvironmentConfigError(str(e)) from e

    return env_path, config


def require_validated_environment(env_name: str) -> tuple[Path, dict]:
    """Load environment with full deployment validation.

    Like require_environment(), but also checks that infrastructure
    has been deployed (terraform state exists).

    Args:
        env_name: Environment name (e.g., "myapp-staging").

    Returns:
        Tuple of (env_path, resolved_config).

    Raises:
        EnvironmentConfigError: If environment is invalid, not deployed,
            or config can't be loaded.
    """
    from ..core.config import load_environment_config  # noqa: PLC0415

    env_path, error = validate_environment_deployed(env_name)
    if error:
        raise EnvironmentConfigError(error)

    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        raise EnvironmentConfigError(str(e)) from e

    return env_path, config
