"""SQLite persistence layer for runs and per-run side state.

Design:
  - Single SQLite file at ~/.t2v_promptgen/runs.db
  - Each Run is one row; auxiliary dicts (creds, qa_reports, intake,
    last_error, dim_critique) are JSON-serialized alongside in side
    columns. Transient state (rewrite_state, cancel_flag, raw_rows)
    is NOT persisted — those die with the process by design.
  - Save is best-effort; failures log but don't crash the request.
  - Load happens once at server startup.

Threading: sqlite3 is thread-safe with check_same_thread=False since
Python 3.4 + we use a per-call connection. Slow but correct for a
local single-user prototype. SQLAlchemy/SQLModel deferred.

Sensitive data: api_key in RUN_CREDS is persisted by default. Users
who don't want this can set env T2V_PERSIST_CREDS=0 to keep creds
in-memory only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .schema import Run


# ---------------------------------------------------------------------------
# Paths + connection
# ---------------------------------------------------------------------------

def db_path() -> Path:
    """~/.t2v_promptgen/runs.db (env override: T2V_PROMPTGEN_DB)."""
    override = os.environ.get("T2V_PROMPTGEN_DB")
    if override:
        return Path(override).expanduser()
    base = Path.home() / ".t2v_promptgen"
    base.mkdir(parents=True, exist_ok=True)
    return base / "runs.db"


_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def _init_db(conn: sqlite3.Connection) -> None:
    """Create tables + indexes if absent. Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            capability_slug TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'generate',
            phase TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            run_json TEXT NOT NULL,
            creds_json TEXT,            -- nullable; api_key stored here if persist enabled
            qa_report_json TEXT,
            dim_critique_json TEXT,
            intake_json TEXT,
            last_error TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_runs_updated ON runs (updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_runs_capability ON runs (capability_slug);
        CREATE INDEX IF NOT EXISTS idx_runs_source ON runs (source);
    """)
    conn.commit()


@contextmanager
def _connection():
    """Get a connection (per-call, thread-safe)."""
    global _INITIALIZED
    path = db_path()
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        isolation_level=None,   # autocommit; we manage txns explicitly
    )
    conn.row_factory = sqlite3.Row
    if not _INITIALIZED:
        with _INIT_LOCK:
            if not _INITIALIZED:
                _init_db(conn)
                _INITIALIZED = True
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Persist creds toggle
# ---------------------------------------------------------------------------

def persist_creds_enabled() -> bool:
    """Whether to write RUN_CREDS to disk. Default True; opt-out via env."""
    return os.environ.get("T2V_PERSIST_CREDS", "1") not in ("0", "false", "no")


# ---------------------------------------------------------------------------
# Save / load — one Run + its side state
# ---------------------------------------------------------------------------

def save_run(
    run: Run,
    creds: dict | None = None,
    qa_report: dict | None = None,
    dim_critique: dict | None = None,
    intake: dict | None = None,
    last_error: str | None = None,
) -> None:
    """Upsert a Run + its side state. Best-effort; failures logged not raised."""
    try:
        with _connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, capability_slug, source, phase,
                                  created_at, updated_at, run_json,
                                  creds_json, qa_report_json,
                                  dim_critique_json, intake_json, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    capability_slug=excluded.capability_slug,
                    source=excluded.source,
                    phase=excluded.phase,
                    updated_at=excluded.updated_at,
                    run_json=excluded.run_json,
                    creds_json=excluded.creds_json,
                    qa_report_json=excluded.qa_report_json,
                    dim_critique_json=excluded.dim_critique_json,
                    intake_json=excluded.intake_json,
                    last_error=excluded.last_error
                """,
                (
                    run.id,
                    run.capability_slug,
                    run.source,
                    run.phase.value,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    run.model_dump_json(),
                    json.dumps(creds) if (creds and persist_creds_enabled()) else None,
                    json.dumps(qa_report) if qa_report else None,
                    json.dumps(dim_critique) if dim_critique else None,
                    json.dumps(intake) if intake else None,
                    last_error,
                ),
            )
    except Exception as exc:
        print(f"[persistence] save_run({run.id}) failed: {exc}", flush=True)


def load_run(run_id: str) -> tuple[Run, dict] | None:
    """Load one Run + side state by id. Returns (run, extras_dict) or None."""
    try:
        with _connection() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not row:
                return None
            return _row_to_run_and_extras(row)
    except Exception as exc:
        print(f"[persistence] load_run({run_id}) failed: {exc}", flush=True)
        return None


def list_runs() -> list[tuple[Run, dict]]:
    """Load every run, newest first. Returns list of (run, extras_dict)."""
    out: list[tuple[Run, dict]] = []
    try:
        with _connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC"
            ).fetchall()
        for row in rows:
            try:
                out.append(_row_to_run_and_extras(row))
            except Exception as exc:
                print(f"[persistence] skipping bad row {row['id']}: {exc}", flush=True)
        return out
    except Exception as exc:
        print(f"[persistence] list_runs failed: {exc}", flush=True)
        return out


def delete_run(run_id: str) -> bool:
    """Delete one run. Returns True if a row was removed."""
    try:
        with _connection() as conn:
            cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[persistence] delete_run({run_id}) failed: {exc}", flush=True)
        return False


def list_run_summaries() -> list[dict]:
    """Quick metadata-only listing for CLI / overview tables.

    Skips loading the full Run pydantic blob (faster for big DBs).
    """
    try:
        with _connection() as conn:
            rows = conn.execute(
                "SELECT id, capability_slug, source, phase, created_at, updated_at "
                "FROM runs ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        print(f"[persistence] list_run_summaries failed: {exc}", flush=True)
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_run_and_extras(row: sqlite3.Row) -> tuple[Run, dict]:
    """Reconstruct Run + extras dict from a DB row."""
    run = Run.model_validate_json(row["run_json"])
    extras: dict = {}
    for key, col in [
        ("creds", "creds_json"),
        ("qa_report", "qa_report_json"),
        ("dim_critique", "dim_critique_json"),
        ("intake", "intake_json"),
    ]:
        val = row[col]
        if val:
            try:
                extras[key] = json.loads(val)
            except json.JSONDecodeError:
                pass
    if row["last_error"]:
        extras["last_error"] = row["last_error"]
    return run, extras


# ---------------------------------------------------------------------------
# Maintenance (CLI / tests)
# ---------------------------------------------------------------------------

def wipe_all_runs() -> int:
    """Delete all runs. Returns count deleted. For CLI/test use only."""
    with _connection() as conn:
        cur = conn.execute("DELETE FROM runs")
        return cur.rowcount


def db_stats() -> dict:
    """Quick health-check info."""
    with _connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        sizes = conn.execute(
            "SELECT source, COUNT(*) AS n FROM runs GROUP BY source"
        ).fetchall()
    return {
        "db_path": str(db_path()),
        "total_runs": n,
        "by_source": {r["source"]: r["n"] for r in sizes},
        "creds_persisted": persist_creds_enabled(),
    }
