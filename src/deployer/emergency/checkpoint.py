"""Checkpoint system for emergency operations.

Saves state before changes to enable recovery/undo of emergency actions.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..utils import get_deployer_root


def get_checkpoint_dir() -> Path:
    """Get path to the checkpoints directory.

    Returns:
        Path to local/checkpoints/
    """
    return get_deployer_root() / "local" / "checkpoints"


@dataclass
class ServiceState:
    """State of an ECS service at checkpoint time."""

    task_definition: str  # Full ARN
    desired_count: int
    running_count: int


@dataclass
class RdsState:
    """State of RDS at checkpoint time."""

    instance_id: str
    status: str


@dataclass
class Checkpoint:
    """Checkpoint representing system state before an emergency action.

    Checkpoints are saved to local/checkpoints/ as JSON files and can be
    used to restore the previous state if needed.
    """

    timestamp: str
    environment: str
    action: str
    reason: str
    services: dict[str, ServiceState] = field(default_factory=dict)
    rds: RdsState | None = None
    filename: str | None = None

    def to_dict(self) -> dict:
        """Convert checkpoint to dictionary for JSON serialization."""
        data = {
            "timestamp": self.timestamp,
            "environment": self.environment,
            "action": self.action,
            "reason": self.reason,
            "state": {
                "services": {name: asdict(state) for name, state in self.services.items()},
            },
        }
        if self.rds:
            data["state"]["rds"] = asdict(self.rds)
        return data

    @classmethod
    def from_dict(cls, data: dict, filename: str | None = None) -> "Checkpoint":
        """Create checkpoint from dictionary (loaded from JSON).

        Args:
            data: Dictionary from JSON file
            filename: Optional filename this was loaded from

        Returns:
            Checkpoint instance
        """
        services = {}
        for name, state in data.get("state", {}).get("services", {}).items():
            services[name] = ServiceState(**state)

        rds = None
        rds_data = data.get("state", {}).get("rds")
        if rds_data:
            rds = RdsState(**rds_data)

        return cls(
            timestamp=data["timestamp"],
            environment=data["environment"],
            action=data["action"],
            reason=data["reason"],
            services=services,
            rds=rds,
            filename=filename,
        )


def generate_checkpoint_filename() -> str:
    """Generate a unique checkpoint filename based on current timestamp.

    Returns:
        Filename like 'emergency-2026-02-04-120000.json'
    """
    now = datetime.now(timezone.utc)
    return f"emergency-{now.strftime('%Y-%m-%d-%H%M%S')}.json"


def create_checkpoint(
    environment: str,
    action: str,
    reason: str,
    services: dict[str, ServiceState],
    rds: RdsState | None,
) -> Checkpoint:
    """Create and save a new checkpoint.

    Args:
        environment: Environment name
        action: Action being performed (rollback, scale, etc.)
        reason: Human-readable reason for the action
        services: Current state of ECS services
        rds: Optional RDS state

    Returns:
        The created Checkpoint
    """
    checkpoint_dir = get_checkpoint_dir()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    filename = generate_checkpoint_filename()
    filepath = checkpoint_dir / filename

    checkpoint = Checkpoint(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        environment=environment,
        action=action,
        reason=reason,
        services=services,
        rds=rds,
        filename=filename,
    )

    with open(filepath, "w") as f:
        json.dump(checkpoint.to_dict(), f, indent=2)

    return checkpoint


def load_checkpoint(filename: str) -> Checkpoint:
    """Load a checkpoint from file.

    Args:
        filename: Checkpoint filename (e.g., 'emergency-2026-02-04-120000.json')

    Returns:
        Loaded Checkpoint

    Raises:
        FileNotFoundError: If checkpoint file doesn't exist
    """
    filepath = get_checkpoint_dir() / filename
    with open(filepath) as f:
        data = json.load(f)
    return Checkpoint.from_dict(data, filename=filename)


def list_checkpoints(environment: str) -> list[Checkpoint]:
    """List all available checkpoints for an environment.

    Args:
        environment: Environment name to filter by

    Returns:
        List of checkpoints, sorted by timestamp (newest first)
    """
    checkpoint_dir = get_checkpoint_dir()
    if not checkpoint_dir.exists():
        return []

    checkpoints = []
    for filepath in checkpoint_dir.glob("emergency-*.json"):
        try:
            with open(filepath) as f:
                data = json.load(f)
            checkpoint = Checkpoint.from_dict(data, filename=filepath.name)
            if checkpoint.environment == environment:
                checkpoints.append(checkpoint)
        except (json.JSONDecodeError, KeyError):
            # Skip invalid checkpoint files
            continue

    # Sort by timestamp, newest first
    checkpoints.sort(key=lambda c: c.timestamp, reverse=True)
    return checkpoints


def cleanup_old_checkpoints(
    keep_count: int = 10,
    keep_days: int = 7,
    *,
    environment: str,
) -> list[str]:
    """Clean up old checkpoints, keeping recent ones.

    Keeps checkpoints that are either:
    - Within the most recent `keep_count` checkpoints
    - Within the last `keep_days` days

    Args:
        keep_count: Minimum number of checkpoints to keep
        keep_days: Minimum number of days to keep checkpoints
        environment: Optional filter by environment

    Returns:
        List of deleted checkpoint filenames
    """
    checkpoints = list_checkpoints(environment)
    if not checkpoints:
        return []

    # Calculate cutoff date
    cutoff = datetime.now(timezone.utc).timestamp() - (keep_days * 24 * 60 * 60)

    deleted = []
    checkpoint_dir = get_checkpoint_dir()

    for i, checkpoint in enumerate(checkpoints):
        # Always keep the most recent `keep_count` checkpoints
        if i < keep_count:
            continue

        # Parse timestamp and check if older than cutoff
        try:
            ts = datetime.fromisoformat(checkpoint.timestamp.replace("Z", "+00:00")).timestamp()
            if ts < cutoff and checkpoint.filename:
                filepath = checkpoint_dir / checkpoint.filename
                filepath.unlink()
                deleted.append(checkpoint.filename)
        except (ValueError, OSError):
            continue

    return deleted
