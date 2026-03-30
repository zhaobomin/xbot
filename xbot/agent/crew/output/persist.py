"""Output persistence for crew execution results.

Directory structure:
  workspace/.xbot/crew_runs/{crew_name}_{timestamp}/
  ├── manifest.json           # Run metadata
  ├── tasks/
  │   ├── 01_task_name.json
  │   └── 02_task_name.md
  ├── artifacts/              # Generated files
  └── errors.log

Features:
- Immediate write after each task
- Streaming for large outputs
- Retention management
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from xbot.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TaskOutputRecord:
    """Record for a single task's output."""

    task_name: str
    status: str
    started_at: datetime
    finished_at: datetime
    output_file: str | None = None
    output_format: str = "raw"
    truncated: bool = False
    repaired: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_name": self.task_name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "output_file": self.output_file,
            "output_format": self.output_format,
            "truncated": self.truncated,
            "repaired": self.repaired,
            "error": self.error,
            "metadata": self.metadata,
        }


@dataclass
class RunManifest:
    """Manifest for a crew run."""

    crew_name: str
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    total_time: float = 0.0
    tasks: list[TaskOutputRecord] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    config_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "crew_name": self.crew_name,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "total_time": self.total_time,
            "tasks": [t.to_dict() for t in self.tasks],
            "variables": self.variables,
            "config_path": self.config_path,
        }


class OutputPersister:
    """Manages persistent storage of crew outputs."""

    # Maximum size before splitting into chunks
    CHUNK_SIZE = 1024 * 1024  # 1MB

    def __init__(
        self,
        workspace: Path | str,
        crew_name: str,
        run_id: str | None = None,
        retention_days: int = 30,
    ):
        """Initialize the persister.

        Args:
            workspace: Base workspace directory
            crew_name: Name of the crew
            run_id: Unique run identifier (auto-generated if None)
            retention_days: Days to keep old runs
        """
        self.workspace = Path(workspace)
        self.crew_name = crew_name
        self.run_id = run_id or self._generate_run_id()
        self.retention_days = retention_days

        # Create run directory structure
        self.run_dir = self.workspace / ".xbot" / "crew_runs" / self.run_id
        self.tasks_dir = self.run_dir / "tasks"
        self.artifacts_dir = self.run_dir / "artifacts"

        # Initialize manifest
        self.manifest = RunManifest(
            crew_name=crew_name,
            run_id=self.run_id,
            started_at=datetime.now(),
        )

        self._initialized = False

    def initialize(self) -> None:
        """Create directory structure and initial manifest."""
        if self._initialized:
            return

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(exist_ok=True)
        self.artifacts_dir.mkdir(exist_ok=True)

        self._save_manifest()
        self._initialized = True

        logger.debug(f"[crew-output] Initialized output directory: {self.run_dir}")

    def save_task_output(
        self,
        task_name: str,
        output: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        output_format: str = "raw",
        truncated: bool = False,
        repaired: bool = False,
        structured_output: dict | None = None,
        artifacts: list[str] | None = None,
    ) -> TaskOutputRecord:
        """Save output from a completed task.

        Args:
            task_name: Name of the task
            output: The output content
            status: Task status (success, failed, skipped)
            started_at: Task start time
            finished_at: Task end time
            output_format: Format of the output
            truncated: Whether output was truncated
            repaired: Whether output was repaired by LLM
            structured_output: Parsed structured data (if applicable)
            artifacts: List of artifact file paths

        Returns:
            TaskOutputRecord for the saved task
        """
        if not self._initialized:
            self.initialize()

        # Determine file extension
        ext = self._get_extension(output_format)
        safe_name = self._safe_filename(task_name)

        # Use task index for ordering
        task_index = len(self.manifest.tasks) + 1
        filename = f"{task_index:02d}_{safe_name}.{ext}"
        output_path = self.tasks_dir / filename

        # Prepare output data
        if output_format == "json" and structured_output:
            output_data = json.dumps(structured_output, indent=2, ensure_ascii=False)
        else:
            output_data = output

        # Write output file
        self._write_file(output_path, output_data)

        # Create record
        record = TaskOutputRecord(
            task_name=task_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            output_file=filename,
            output_format=output_format,
            truncated=truncated,
            repaired=repaired,
        )

        # Update manifest
        self.manifest.tasks.append(record)
        self._save_manifest()

        logger.debug(f"[crew-output] Saved task output: {filename}")

        return record

    def save_artifact(
        self,
        artifact_name: str,
        content: str | bytes,
        content_type: str = "text/plain",
    ) -> Path:
        """Save an artifact file.

        Args:
            artifact_name: Name for the artifact
            content: File content (string or bytes)
            content_type: MIME type of content

        Returns:
            Path to saved artifact
        """
        if not self._initialized:
            self.initialize()

        safe_name = self._safe_filename(artifact_name)
        artifact_path = self.artifacts_dir / safe_name

        if isinstance(content, str):
            artifact_path.write_text(content, encoding="utf-8")
        else:
            artifact_path.write_bytes(content)

        logger.debug(f"[crew-output] Saved artifact: {safe_name}")

        return artifact_path

    def finalize(self, status: str = "completed") -> None:
        """Finalize the run and save final manifest.

        Args:
            status: Final status (completed, failed, aborted)
        """
        if not self._initialized:
            return

        self.manifest.finished_at = datetime.now()
        self.manifest.status = status

        if self.manifest.started_at and self.manifest.finished_at:
            delta = self.manifest.finished_at - self.manifest.started_at
            self.manifest.total_time = delta.total_seconds()

        self._save_manifest()

        # Clean up old runs
        self._cleanup_old_runs()

        logger.info(
            f"[crew-output] Run finalized: {self.run_id} "
            f"(status={status}, tasks={len(self.manifest.tasks)}, "
            f"time={self.manifest.total_time:.1f}s)"
        )

    def get_run_summary(self) -> dict[str, Any]:
        """Get a summary of the run."""
        return {
            "run_id": self.run_id,
            "crew_name": self.crew_name,
            "status": self.manifest.status,
            "total_tasks": len(self.manifest.tasks),
            "successful_tasks": sum(1 for t in self.manifest.tasks if t.status == "success"),
            "failed_tasks": sum(1 for t in self.manifest.tasks if t.status == "failed"),
            "total_time": self.manifest.total_time,
            "output_dir": str(self.run_dir),
        }

    def _generate_run_id(self) -> str:
        """Generate a unique run ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{self.crew_name}_{timestamp}"

    def _get_extension(self, output_format: str) -> str:
        """Get file extension for output format."""
        extensions = {
            "json": "json",
            "markdown": "md",
            "structured": "json",
            "raw": "txt",
        }
        return extensions.get(output_format, "txt")

    def _safe_filename(self, name: str) -> str:
        """Convert name to safe filename."""
        # Replace unsafe characters
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return safe.lower().strip("_")

    def _write_file(self, path: Path, content: str) -> None:
        """Write content to file with atomic write."""
        # Use atomic write to avoid partial files
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            Path(tmp_path).replace(path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _save_manifest(self) -> None:
        """Save the manifest file."""
        manifest_path = self.run_dir / "manifest.json"
        content = json.dumps(self.manifest.to_dict(), indent=2, ensure_ascii=False)
        self._write_file(manifest_path, content)

    def _cleanup_old_runs(self) -> None:
        """Remove old runs beyond retention period."""
        runs_dir = self.workspace / ".xbot" / "crew_runs"
        if not runs_dir.exists():
            return

        cutoff = datetime.now().timestamp() - (self.retention_days * 24 * 60 * 60)

        for run_path in runs_dir.iterdir():
            if run_path == self.run_dir:
                continue

            # Check manifest for timestamp
            manifest_path = run_path / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        data = json.load(f)
                    started_str = data.get("started_at", "")
                    if not started_str:
                        continue  # Skip if no valid timestamp
                    started = datetime.fromisoformat(started_str)
                    if started.timestamp() < cutoff:
                        logger.info(f"[crew-output] Removing old run: {run_path.name}")
                        # Remove directory
                        import shutil
                        shutil.rmtree(run_path)
                except (ValueError, KeyError, json.JSONDecodeError):
                    pass  # Invalid manifest, skip


def create_persister(
    workspace: Path | str,
    crew_name: str,
) -> OutputPersister:
    """Convenience function to create an OutputPersister.

    Args:
        workspace: Base workspace directory
        crew_name: Name of the crew

    Returns:
        Initialized OutputPersister
    """
    persister = OutputPersister(workspace, crew_name)
    persister.initialize()
    return persister