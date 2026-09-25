"""Migration optimization utilities.

This module provides functionality to skip migrations when no migration files
have changed since the last successful deployment.

The migrations hash a deploy acts on is taken once, before any image is built
(:meth:`MigrationsSnapshot.take`), because that is the tree the images are
built from. Hashing the tree again at migrate time would describe whatever the
checkout holds by then -- in a shared checkout, possibly migrations the image
does not contain -- and storing that hash would record a migrate that never
happened. The later hash is used only to refuse the deploy when the two differ
(:meth:`MigrationsSnapshot.verify_unchanged`).
"""

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Self

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

    except OSError:
        # The migrations directory could not be read: run migrations to be
        # safe (should_skip_migrations treats None that way). Only OSError --
        # a bug in the hashing itself must not silently switch skip-detection
        # off for every deploy.
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


class MigrationsChangedDuringDeployError(RuntimeError):
    """The source tree's migrations changed between the image build and migrate.

    The images were built from the tree as it stood when the deploy began; a
    migrate run now would use an image that may not hold the tree's
    migrations, and the hash it stored would describe a tree that was never
    migrated. The only safe answer is to stop and deploy again.
    """


@dataclass(frozen=True)
class MigrationsSnapshot:
    """The migrations hash of a source tree, taken before the images are built.

    Attributes:
        source_dir: The application source directory that was hashed.
        migrations_hash: Its migrations hash, or None when there are no
            migration files or they could not be read (always migrate).
    """

    source_dir: Path
    migrations_hash: str | None

    @classmethod
    def take(cls, source_dir: Path) -> Self:
        """Hash the source tree's migrations as they stand now.

        Args:
            source_dir: Resolved path to the application source directory.

        Returns:
            The snapshot to carry through the rest of the deploy.
        """
        return cls(source_dir, compute_migrations_hash(source_dir))

    def verify_unchanged(self) -> None:
        """Refuse to go on if the tree's migrations moved since the snapshot.

        Raises:
            MigrationsChangedDuringDeployError: The tree's migrations hash now
                differs from the one taken before the build.
        """
        current_hash = compute_migrations_hash(self.source_dir)
        if current_hash != self.migrations_hash:
            raise MigrationsChangedDuringDeployError(
                "The source tree's migrations changed during the deploy "
                f"(hash before the build: {self.migrations_hash}, now: {current_hash}). "
                "The images may not contain the current migrations, so migrate was "
                "not run and no migrations hash was stored. Re-run the deploy."
            )


def should_skip_migrations(
    migrations_hash: str | None,
    app_name: str,
    environment: str,
) -> bool:
    """Check if migrations can be skipped.

    Compares the migrations hash taken before the build with the stored hash
    from the last successful migrate. If they match, migrations can be skipped.

    Args:
        migrations_hash: The snapshot's hash (``MigrationsSnapshot.migrations_hash``).
        app_name: Application name.
        environment: Environment name.

    Returns:
        True if migrations can be skipped.
    """
    if migrations_hash is None:
        # Couldn't compute hash, run migrations to be safe
        return False

    stored_hash = get_stored_migrations_hash(app_name, environment)

    if stored_hash is None:
        # No stored hash, this is the first deploy or hash was never stored
        log("No stored migrations hash found, will run migrations")
        return False

    if migrations_hash == stored_hash:
        log_success(f"Migrations unchanged (hash: {migrations_hash}), skipping")
        return True

    log(f"Migrations changed ({stored_hash} -> {migrations_hash}), will run migrations")
    return False
