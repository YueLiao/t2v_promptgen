"""SQLite-backed persistence for in-flight Run state.

One row per Run, JSON-serialized Run model. Use save/load on every phase
transition so kill-and-resume is safe.
"""
from __future__ import annotations

from pathlib import Path

from .schema import Run

# Default DB location; CLI may override
DEFAULT_DB_PATH = Path.home() / ".t2v_promptgen" / "runs.db"


def init_db(path: Path = DEFAULT_DB_PATH) -> None:
    """Create runs table if not exists. Idempotent."""
    raise NotImplementedError


def save(run: Run, path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert run state (serialized to JSON) keyed by run.id."""
    raise NotImplementedError


def load(run_id: str, path: Path = DEFAULT_DB_PATH) -> Run:
    """Load a Run by id. Raises if not found."""
    raise NotImplementedError


def list_runs(path: Path = DEFAULT_DB_PATH) -> list[Run]:
    """List all runs (latest first)."""
    raise NotImplementedError


def delete(run_id: str, path: Path = DEFAULT_DB_PATH) -> None:
    """Hard delete a run from DB."""
    raise NotImplementedError
