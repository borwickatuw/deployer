"""Environment discovery utilities."""

import os
from pathlib import Path


def get_deployer_root() -> Path:
    """Get the root directory of the deployer repository.

    Returns:
        Path to the deployer root directory.
    """
    # This file is at deployer/src/deployer/utils/environment.py
    return Path(__file__).parent.parent.parent.parent


def get_environments_dir() -> Path:
    """Get the environments directory.

    Requires DEPLOYER_ENVIRONMENTS_DIR environment variable to be set.

    Returns:
        Path to the environments directory.

    Raises:
        RuntimeError: If DEPLOYER_ENVIRONMENTS_DIR is not set.
    """
    env_dir = os.environ.get("DEPLOYER_ENVIRONMENTS_DIR")
    if not env_dir:
        raise RuntimeError(
            "DEPLOYER_ENVIRONMENTS_DIR environment variable is not set.\n"
            "Set it in your .env file:\n"
            "  DEPLOYER_ENVIRONMENTS_DIR=~/deployer-environments"
        )
    return Path(env_dir).expanduser()


def get_environment_path(env_name: str) -> Path:
    """Get the path to a specific environment directory.

    Args:
        env_name: Name of the environment.

    Returns:
        Path to the environment directory.
    """
    return get_environments_dir() / env_name


def get_all_environments(environments_dir: Path) -> list[str]:
    """Find all ECS environment directories.

    An ECS environment is identified by the presence of a config.toml file.
    This excludes bootstrap directories and other non-ECS infrastructure.

    Args:
        environments_dir: Directory containing environment subdirectories.

    Returns:
        Sorted list of all environment names.
    """
    envs = []

    if not environments_dir.exists():
        return envs

    for env_dir in environments_dir.iterdir():
        if (
            env_dir.is_dir()
            and not env_dir.name.startswith(".")
            and (env_dir / "config.toml").exists()
        ):
            envs.append(env_dir.name)

    return sorted(envs)


def validate_environment_deployed(env_name: str) -> tuple[Path | None, str | None]:
    """Check if environment directory and terraform state exist.

    Args:
        env_name: Name of the environment.

    Returns:
        Tuple of (env_path, None) if valid, or (None, error_message) if not.
    """
    env_path = get_environment_path(env_name)

    if not env_path.exists():
        return None, f"Environment directory not found: {env_path}"

    state_file = env_path / "terraform.tfstate"
    if not state_file.exists():
        return None, f"Environment '{env_name}' is not deployed"

    return env_path, None


def ensure_environments_symlinks() -> list[str]:
    """Ensure symlinks to deployer modules exist in the environments directory.

    Symlinks are needed so that module sources like '../modules' resolve correctly.

    Creates symlinks for:
    - modules -> deployer/modules
    - main.tf -> deployer/main.tf
    - variables.tf -> deployer/variables.tf
    - outputs.tf -> deployer/outputs.tf

    Returns:
        List of created symlink names (empty if none created).
    """
    env_dir = get_environments_dir()
    deployer_root = get_deployer_root()

    created = []
    symlinks = {
        "modules": deployer_root / "modules",
        "main.tf": deployer_root / "main.tf",
        "variables.tf": deployer_root / "variables.tf",
        "outputs.tf": deployer_root / "outputs.tf",
    }

    for name, target in symlinks.items():
        link_path = env_dir / name
        if not link_path.exists():
            # Create relative symlink
            try:
                relative_target = os.path.relpath(target, env_dir)
                link_path.symlink_to(relative_target)
                created.append(name)
            except OSError:
                # Symlink creation failed (e.g., permissions), skip silently
                pass

    return created
