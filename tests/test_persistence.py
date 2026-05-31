"""Tests for core.persistence — SQLite round-trip, side-state, deletion."""
import os
import tempfile
from datetime import datetime

import pytest

from t2v_promptgen.core.schema import Phase, Run
from t2v_promptgen.core.rewrite_schema import (
    RewriteDirective, SourceFile, SourcePrompt, Transform,
)


@pytest.fixture
def tmp_db(monkeypatch):
    """Each test gets a fresh in-memory DB via env override."""
    path = tempfile.mkstemp(suffix=".db")[1]
    monkeypatch.setenv("T2V_PROMPTGEN_DB", path)
    # Force re-init of the module-level guard
    import t2v_promptgen.core.persistence as P
    P._INITIALIZED = False
    yield path
    P._INITIALIZED = False
    try:
        os.unlink(path)
    except OSError:
        pass


def _fresh_run(rid="r1", source="generate", phase=Phase.P1_DIMENSIONS) -> Run:
    now = datetime.now()
    return Run(
        id=rid, capability_slug="human_hand",
        created_at=now, updated_at=now, phase=phase, source=source,
    )


# ---------- round-trip ----------

def test_round_trip_minimal(tmp_db):
    from t2v_promptgen.core.persistence import save_run, load_run
    r = _fresh_run()
    save_run(r)
    pair = load_run("r1")
    assert pair is not None
    loaded, extras = pair
    assert loaded.id == "r1"
    assert loaded.capability_slug == "human_hand"
    assert loaded.source == "generate"
    assert extras == {}


def test_round_trip_with_all_side_state(tmp_db):
    from t2v_promptgen.core.persistence import save_run, load_run
    r = _fresh_run()
    save_run(
        r,
        creds={"api_key": "sk-test", "provider": "deepseek"},
        qa_report={"pass_rate": 0.9, "total": 60},
        dim_critique={"score": 8, "verdict": "good_to_go"},
        intake={"slug": "human_hand", "confidence": "high"},
        last_error="something happened",
    )
    _, extras = load_run("r1")
    assert extras["creds"]["api_key"] == "sk-test"
    assert extras["qa_report"]["pass_rate"] == 0.9
    assert extras["dim_critique"]["score"] == 8
    assert extras["intake"]["slug"] == "human_hand"
    assert extras["last_error"] == "something happened"


def test_round_trip_rewrite_run(tmp_db):
    from t2v_promptgen.core.persistence import save_run, load_run
    r = _fresh_run(rid="rw1", source="rewrite", phase=Phase.P4_REVIEW)
    r.source_file = SourceFile(filename="a.json", format="json",
                                size_bytes=100, row_count=2)
    r.source_prompts = [
        SourcePrompt(source_id="1", original_text="hi"),
        SourcePrompt(source_id="2", original_text="bye"),
    ]
    r.rewrite_directive = RewriteDirective(
        transforms=[Transform(id="add_temporal", name_zh="加时序段数",
                              params={"segments": "3 段"}, order=0)],
        free_text="保持原意",
    )
    save_run(r)
    loaded, _ = load_run("rw1")
    assert loaded.source == "rewrite"
    assert loaded.source_file.filename == "a.json"
    assert len(loaded.source_prompts) == 2
    assert loaded.rewrite_directive.transforms[0].id == "add_temporal"
    assert loaded.rewrite_directive.free_text == "保持原意"


def test_load_nonexistent_returns_none(tmp_db):
    from t2v_promptgen.core.persistence import load_run
    assert load_run("nope") is None


# ---------- list ----------

def test_list_runs_sorted_by_updated(tmp_db):
    from t2v_promptgen.core.persistence import save_run, list_runs
    import time
    save_run(_fresh_run("a"))
    time.sleep(0.01)
    save_run(_fresh_run("b"))
    runs = list_runs()
    ids = [r.id for r, _ in runs]
    assert ids == ["b", "a"]   # newest first


def test_list_run_summaries_quick(tmp_db):
    from t2v_promptgen.core.persistence import save_run, list_run_summaries
    save_run(_fresh_run("a"))
    save_run(_fresh_run("b", source="rewrite"))
    sums = list_run_summaries()
    assert len(sums) == 2
    assert {s["source"] for s in sums} == {"generate", "rewrite"}
    # No 'run_json' field in summaries
    assert "run_json" not in sums[0]


# ---------- delete ----------

def test_delete_existing(tmp_db):
    from t2v_promptgen.core.persistence import save_run, delete_run, load_run
    save_run(_fresh_run("a"))
    assert delete_run("a") is True
    assert load_run("a") is None


def test_delete_nonexistent(tmp_db):
    from t2v_promptgen.core.persistence import delete_run
    assert delete_run("nope") is False


# ---------- upsert ----------

def test_upsert_overwrites(tmp_db):
    from t2v_promptgen.core.persistence import save_run, load_run
    r = _fresh_run()
    r.phase = Phase.P1_DIMENSIONS
    save_run(r)
    r.phase = Phase.P4_REVIEW
    save_run(r)
    loaded, _ = load_run("r1")
    assert loaded.phase == Phase.P4_REVIEW


# ---------- creds toggle ----------

def test_creds_persisted_by_default(tmp_db):
    from t2v_promptgen.core.persistence import save_run, load_run
    save_run(_fresh_run(), creds={"api_key": "sk-x"})
    _, extras = load_run("r1")
    assert extras["creds"]["api_key"] == "sk-x"


def test_creds_not_persisted_when_disabled(tmp_db, monkeypatch):
    monkeypatch.setenv("T2V_PERSIST_CREDS", "0")
    from t2v_promptgen.core.persistence import save_run, load_run
    save_run(_fresh_run(), creds={"api_key": "sk-secret"})
    _, extras = load_run("r1")
    assert "creds" not in extras


# ---------- stats ----------

def test_db_stats(tmp_db):
    from t2v_promptgen.core.persistence import save_run, db_stats
    save_run(_fresh_run("a"))
    save_run(_fresh_run("b", source="rewrite"))
    s = db_stats()
    assert s["total_runs"] == 2
    assert s["by_source"] == {"generate": 1, "rewrite": 1}
    assert "db_path" in s
