"""Emergency action logging.

Provides an append-only log file for auditing all emergency operations.
"""

from datetime import datetime, timezone
from pathlib import Path

from ..utils import get_deployer_root


def get_log_path() -> Path:
    """Get path to the emergency log file.

    Returns:
        Path to local/emergency.log
    """
    return get_deployer_root() / "local" / "emergency.log"


class EmergencyLogger:
    """Append-only logger for emergency operations.

    All actions are logged with timestamps, environment, and action details.

    Format:
        2026-02-04T12:00:00Z [myapp-production] ACTION: rollback
        2026-02-04T12:00:01Z [myapp-production] ECS: Rolling back web from revision 45 to 44

    Usage:
        logger = EmergencyLogger("myapp-production")
        logger.action("rollback")
        logger.ecs("Rolling back web from revision 45 to 44")
        logger.success("Rollback completed")
    """

    def __init__(self, environment: str):
        """Initialize the logger.

        Args:
            environment: Environment name (e.g., "myapp-production")
        """
        self.environment = environment
        self.log_path = get_log_path()
        # Ensure log directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        """Get current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _write(self, category: str, message: str) -> None:
        """Write a log entry.

        Args:
            category: Log category (ACTION, ECS, RDS, CHECKPOINT, etc.)
            message: Log message
        """
        timestamp = self._timestamp()
        line = f"{timestamp} [{self.environment}] {category}: {message}\n"
        with open(self.log_path, "a") as f:
            f.write(line)

    def action(self, action_name: str) -> None:
        """Log the start of an action.

        Args:
            action_name: Name of the action (rollback, scale, snapshot, etc.)
        """
        self._write("ACTION", action_name)

    def checkpoint(self, message: str) -> None:
        """Log a checkpoint operation.

        Args:
            message: Checkpoint message
        """
        self._write("CHECKPOINT", message)

    def ecs(self, message: str) -> None:
        """Log an ECS operation.

        Args:
            message: ECS operation message
        """
        self._write("ECS", message)

    def rds(self, message: str) -> None:
        """Log an RDS operation.

        Args:
            message: RDS operation message
        """
        self._write("RDS", message)

    def success(self, message: str) -> None:
        """Log a successful operation.

        Args:
            message: Success message
        """
        self._write("SUCCESS", message)

    def error(self, message: str) -> None:
        """Log an error.

        Args:
            message: Error message
        """
        self._write("ERROR", message)

    def info(self, message: str) -> None:
        """Log an informational message.

        Args:
            message: Info message
        """
        self._write("INFO", message)
