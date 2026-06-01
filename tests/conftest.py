"""Pytest fixtures.

Session-level: redirect persistence to a temporary SQLite file so tests
don't read/write the user's real `~/.t2v_promptgen/runs.db`. This was
S8 in the design audit — `tests/test_ui_endpoints.py` mutates the live
`RUNS` singleton, which under the previous setup also persisted those
mutations into the production DB.

`T2V_PROMPTGEN_DB` is honored by `core/persistence._db_path()` so
setting it before the persistence module is first imported routes all
SQLite IO to the temp file.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_db():
    """Point T2V_PROMPTGEN_DB at a per-session tmp file BEFORE app import.

    Autouse so every test in the suite gets the isolation without
    opting in. Cleans up the file after the session.
    """
    # If user already set the env var (e.g. CI), respect that.
    if os.environ.get("T2V_PROMPTGEN_DB"):
        yield
        return

    tmpdir = tempfile.mkdtemp(prefix="t2v_promptgen_tests_")
    db_path = Path(tmpdir) / "runs.db"
    os.environ["T2V_PROMPTGEN_DB"] = str(db_path)

    yield

    # Cleanup
    try:
        if db_path.exists():
            db_path.unlink()
        Path(tmpdir).rmdir()
    except OSError:
        pass
    os.environ.pop("T2V_PROMPTGEN_DB", None)
