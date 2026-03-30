"""Migration optimization utilities.

This module provides functionality to skip migrations when no migration files
have changed since the last successful deployment.
"""

import hashlib
import subprocess
from pathlib import Path

from ..aws import ssm
from ..utils import log, log_success, log_warning


def compute_migrations_hash(source_dir: Path) -> str | None:
    """Compute a hash of all migration files in the source directory.

    Uses git to compute a tree hash of all */migrations/ directories,
    which is fast and reliable. Falls back to file hashing if git fails.

    Args:
        source_dir: Path to the application source directory.

    Returns:
        A hash string representing the current state of migrations,
        or None if hashing fails.
    """
    source_dir = Path(source_dir).resolve()

    # Try git first (fast and reliable)
    try:
        # Get all migration files using git ls-files with grep
        # This handles any nesting depth (e.g., app/migrations/, pkg/app/migrations/)
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=source_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Filter to only migration .py files (excluding __pycache__)
        all_files = result.stdout.strip().split("\n")
        migration_files = [
            f
            for f in all_files
            if "/migrations/" in f and f.endswith(".py") and "__pycache__" not in f
        ]

        if not migration_files:
            # No migration files found
            return None

        # Get the combined hash of all migration file contents
        result = subprocess.run(
            ["git", "hash-object", "--stdin-paths"],
            input="\n".join(migration_files),
            cwd=source_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Hash all the individual file hashes to get one combined hash
        file_hashes = result.stdout.strip()
        if file_hashes:
            combined = hashlib.sha256(file_hashes.encode()).hexdigest()[:16]
            return combined

    except (subprocess.CalledProcessError, FileNotFoundError):
        # Git not available or not a git repo, fall back to file hashing
        pass

    # Fallback: manually hash migration files
    try:
        migration_files = []
        for migrations_dir in source_dir.glob("*/migrations"):
            if ".venv" in str(migrations_dir) or "site-packages" in str(migrations_dir):
                continue
            for py_file in sorted(migrations_dir.glob("*.py")):
                migration_files.append(py_file)

        if not migration_files:
            return None

        hasher = hashlib.sha256()
        for filepath in sorted(migration_files):
            hasher.update(filepath.name.encode())
            hasher.update(filepath.read_bytes())

        return hasher.hexdigest()[:16]

    except Exception:
        return None


def _get_migrations_hash_param_name(app_name: str, environment: str) -> str:
    """Get the SSM parameter name for storing migration hash.

    Args:
        app_name: Application name.
        environment: Environment name (staging, production).

    Returns:
        SSM parameter name.
    """
    return f"/{app_name}/{environment}/last-migrations-hash"


def get_stored_migrations_hash(app_name: str, environment: str) -> str | None:
    """Get the stored migrations hash from SSM.

    Args:
        app_name: Application name.
        environment: Environment name.

    Returns:
        The stored hash, or None if not found.
    """
    param_name = _get_migrations_hash_param_name(app_name, environment)
    value, error = ssm.get_parameter(param_name)
    if error:
        # Parameter doesn't exist yet, that's fine
        return None
    return value


def store_migrations_hash(app_name: str, environment: str, hash_value: str) -> bool:
    """Store the migrations hash in SSM.

    Args:
        app_name: Application name.
        environment: Environment name.
        hash_value: The hash to store.

    Returns:
        True if successful, False otherwise.
    """
    param_name = _get_migrations_hash_param_name(app_name, environment)
    success, error = ssm.put_parameter(
        name=param_name,
        value=hash_value,
        description="Hash of migration files from last successful deployment",
        overwrite=True,
    )
    if not success:
        log_warning(f"Failed to store migrations hash: {error}")
    return success


def should_skip_migrations(
    source_dir: Path,
    app_name: str,
    environment: str,
) -> tuple[bool, str | None]:
    """Check if migrations can be skipped.

    Compares the current migrations hash with the stored hash from the
    last successful deployment. If they match, migrations can be skipped.

    Args:
        source_dir: Path to the application source directory.
        app_name: Application name.
        environment: Environment name.

    Returns:
        Tuple of (should_skip, current_hash).
        should_skip is True if migrations can be skipped.
        current_hash is the computed hash (for storing after successful migration).
    """
    current_hash = compute_migrations_hash(source_dir)

    if current_hash is None:
        # Couldn't compute hash, run migrations to be safe
        return False, None

    stored_hash = get_stored_migrations_hash(app_name, environment)

    if stored_hash is None:
        # No stored hash, this is the first deploy or hash was never stored
        log("No stored migrations hash found, will run migrations")
        return False, current_hash

    if current_hash == stored_hash:
        log_success(f"Migrations unchanged (hash: {current_hash}), skipping")
        return True, current_hash

    log(f"Migrations changed ({stored_hash} -> {current_hash}), will run migrations")
    return False, current_hash
