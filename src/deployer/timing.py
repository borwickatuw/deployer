"""Deployment timing infrastructure for measuring deployment speed."""

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class StepTiming:
    """Timing data for a single deployment step."""

    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    success: bool = True
    error: str | None = None
    sub_steps: list["StepTiming"] = field(default_factory=list)

    def finish(self, success: bool = True, error: str | None = None) -> None:
        """Mark the step as finished."""
        self.end_time = time.time()
        self.success = success
        self.error = error

    @property
    def duration_seconds(self) -> float:
        """Calculate duration in seconds."""
        if self.end_time == 0.0:
            return 0.0
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "name": self.name,
            "duration_seconds": round(self.duration_seconds, 2),
            "success": self.success,
        }
        if self.error:
            result["error"] = self.error
        if self.sub_steps:
            result["sub_steps"] = [s.to_dict() for s in self.sub_steps]
        return result


@dataclass
class DeploymentTimingReport:
    """Complete timing report for a deployment run."""

    run_id: str
    start_time: float = 0.0
    end_time: float = 0.0
    time_to_visible: float | None = None
    time_to_stable: float | None = None
    steps: list[StepTiming] = field(default_factory=list)

    @property
    def total_duration_seconds(self) -> float:
        """Calculate total deployment duration."""
        if self.end_time == 0.0:
            return 0.0
        return self.end_time - self.start_time

    @property
    def visibility_gap_seconds(self) -> float | None:
        """Calculate gap between visibility and stability."""
        if self.time_to_visible is None or self.time_to_stable is None:
            return None
        return self.time_to_stable - self.time_to_visible

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "run_id": self.run_id,
            "total_duration_seconds": round(self.total_duration_seconds, 2),
            "steps": [s.to_dict() for s in self.steps],
        }
        if self.time_to_visible is not None:
            result["time_to_visible_seconds"] = round(self.time_to_visible, 2)
        if self.time_to_stable is not None:
            result["time_to_stable_seconds"] = round(self.time_to_stable, 2)
        if self.visibility_gap_seconds is not None:
            result["visibility_gap_seconds"] = round(self.visibility_gap_seconds, 2)
        return result

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def save_json(self, path: Path) -> None:
        """Save report to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json())

class DeploymentTimer:
    """Context manager for timing deployment steps."""

    def __init__(self, run_id: str):
        """Initialize timer with a run ID.

        Args:
            run_id: Unique identifier for this deployment run.
        """
        self.report = DeploymentTimingReport(run_id=run_id)
        self._current_step: StepTiming | None = None

    def start(self) -> None:
        """Start the deployment timer."""
        self.report.start_time = time.time()

    def finish(self) -> None:
        """Finish the deployment timer."""
        self.report.end_time = time.time()

    @contextmanager
    def step(self, name: str) -> Iterator[StepTiming]:
        """Time a deployment step.

        Args:
            name: Name of the step being timed.

        Yields:
            StepTiming object for this step.
        """
        step = StepTiming(name=name, start_time=time.time())
        self._current_step = step
        try:
            yield step
            step.finish(success=True)
        except Exception as e:
            step.finish(success=False, error=str(e))
            raise
        finally:
            self.report.steps.append(step)
            self._current_step = None

    @contextmanager
    def sub_step(self, name: str) -> Iterator[StepTiming]:
        """Time a sub-step within the current step.

        Must be called within a step() context.

        Args:
            name: Name of the sub-step.

        Yields:
            StepTiming object for this sub-step.
        """
        if self._current_step is None:
            raise RuntimeError("sub_step must be called within a step context")

        sub = StepTiming(name=name, start_time=time.time())
        try:
            yield sub
            sub.finish(success=True)
        except Exception as e:
            sub.finish(success=False, error=str(e))
            raise
        finally:
            self._current_step.sub_steps.append(sub)


# Global timer instance for optional use in modules
_global_timer: DeploymentTimer | None = None


def get_timer() -> DeploymentTimer | None:
    """Get the global deployment timer, if set."""
    return _global_timer


def set_timer(timer: DeploymentTimer | None) -> None:
    """Set the global deployment timer.

    Args:
        timer: Timer instance to use globally, or None to clear.
    """
    global _global_timer
    _global_timer = timer
