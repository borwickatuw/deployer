"""Environment to deploy.toml link management.

Links are stored in local/environments.toml (gitignored) and map environment
names to deploy.toml paths. This allows users to avoid specifying --deploy-toml
on every command.

Example local/environments.toml:
    [myapp-staging]
    deploy_toml = "~/code/myapp/deploy.toml"

    [otherapp-staging]
    deploy_toml = "~/code/otherapp/deploy.toml"
"""

import tomllib
from pathlib import Path

import tomli_w

from .environment import get_deployer_root


def get_links_file() -> Path:
    """Get the path to the local environments.toml file."""
    return get_deployer_root() / "local" / "environments.toml"


# pysmelly: ignore return-none-instead-of-raise — lookup function, None means "not linked"
def get_linked_deploy_toml(environment: str) -> Path | None:
    """Look up the deploy.toml path for an environment.

    Args:
        environment: Environment name (e.g., 'myapp-staging').

    Returns:
        Path to deploy.toml if linked, None if not found.
    """
    links_file = get_links_file()
    if not links_file.exists():
        return None

    try:
        with open(links_file, "rb") as f:
            links = tomllib.load(f)
    except Exception:  # noqa: BLE001 — missing/corrupt links file is not fatal
        return None

    env_config = links.get(environment)
    if not env_config:
        return None

    deploy_toml = env_config.get("deploy_toml")
    if not deploy_toml:
        return None

    return Path(deploy_toml).expanduser()


def set_linked_deploy_toml(environment: str, deploy_toml_path: Path) -> None:
    """Save a link between an environment and deploy.toml.

    Args:
        environment: Environment name (e.g., 'myapp-staging').
        deploy_toml_path: Path to the deploy.toml file.
    """
    links_file = get_links_file()

    # Ensure local/ directory exists
    links_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing links or start fresh
    links: dict = {}
    if links_file.exists():
        try:
            with open(links_file, "rb") as f:
                links = tomllib.load(f)
        except Exception:  # noqa: BLE001 — corrupt links file, start fresh
            links = {}

    # Convert path to string with ~ for home directory
    path_str = str(deploy_toml_path.resolve())
    home = str(Path.home())
    if path_str.startswith(home):
        path_str = "~" + path_str[len(home) :]

    # Update the link
    links[environment] = {"deploy_toml": path_str}

    # Write back
    with open(links_file, "wb") as f:
        tomli_w.dump(links, f)


def unlink_deploy_toml(environment: str) -> bool:
    """Remove the link for an environment.

    Args:
        environment: Environment name (e.g., 'myapp-staging').

    Returns:
        True if link was removed, False if it didn't exist.
    """
    links_file = get_links_file()
    if not links_file.exists():
        return False

    try:
        with open(links_file, "rb") as f:
            links = tomllib.load(f)
    except Exception:  # noqa: BLE001 — corrupt links file
        return False

    if environment not in links:
        return False

    del links[environment]

    # Write back
    with open(links_file, "wb") as f:
        tomli_w.dump(links, f)

    return True


def get_all_links() -> dict[str, str]:
    """Get all environment to deploy.toml links.

    Returns:
        Dict mapping environment names to deploy.toml paths.
    """
    links_file = get_links_file()
    if not links_file.exists():
        return {}

    try:
        with open(links_file, "rb") as f:
            links = tomllib.load(f)
    except Exception:  # noqa: BLE001 — corrupt links file
        return {}

    return {
        env: config.get("deploy_toml", "")
        for env, config in links.items()
        if config.get("deploy_toml")
    }
