"""Tests for CLI argparse + non-LLM commands (list/show/delete/stats/clone)."""
import json
import os
import tempfile
from datetime import datetime

import pytest

from t2v_promptgen.cli import (
    _build_manifest,
    _tag_library_hash,
    build_parser,
)
from t2v_promptgen.core.schema import Phase, Run


@pytest.fixture
def tmp_db(monkeypatch):
    path = tempfile.mkstemp(suffix=".db")[1]
    monkeypatch.setenv("T2V_PROMPTGEN_DB", path)
    import t2v_promptgen.core.persistence as P
    P._INITIALIZED = False
    yield path
    P._INITIALIZED = False
    try:
        os.unlink(path)
    except OSError:
        pass


def _run(rid="r1", **kw):
    now = datetime.now()
    return Run(
        id=rid, capability_slug="x",
        created_at=now, updated_at=now,
        phase=Phase.P1_DIMENSIONS,
        **kw,
    )


# ---------- argparse wiring ----------

def test_parser_list():
    p = build_parser()
    args = p.parse_args(["list"])
    assert args.command == "list"


def test_parser_create_required_description():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["create"])  # missing --description


def test_parser_create_defaults():
    p = build_parser()
    args = p.parse_args(["create", "--description", "x"])
    assert args.description == "x"
    assert args.size == "auto"
    assert args.provider == "deepseek"
    assert args.model_p1 == "deepseek-chat"
    assert args.seed is None
    assert args.skip_qa is False


def test_parser_create_with_seed():
    p = build_parser()
    args = p.parse_args(["create", "--description", "x", "--seed", "42"])
    assert args.seed == 42


def test_parser_clone():
    p = build_parser()
    args = p.parse_args(["clone", "abc12345", "--size", "80"])
    assert args.run_id == "abc12345"
    assert args.size == 80


def test_parser_unknown_subcommand():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["unknown"])


# ---------- tag library hash is stable ----------

def test_tag_library_hash_stable():
    """Same taxonomy → same hash. Critical for manifest reproducibility."""
    h1 = _tag_library_hash()
    h2 = _tag_library_hash()
    assert h1 == h2
    assert len(h1) == 16  # short sha256
    assert all(c in "0123456789abcdef" for c in h1)


# ---------- manifest schema ----------

def test_manifest_fields():
    r = _run()
    r.target_set_size = 60
    r.provider = "deepseek"
    r.model = "chat / chat"
    m = _build_manifest(r, seed=42)
    assert m["tool_version"]
    assert m["schema_version"] == 1
    assert m["run_id"] == "r1"
    assert m["seed"] == 42
    assert m["target_set_size"] == 60
    assert m["provider"] == "deepseek"
    assert "tag_library_sha256_short" in m
    assert m["prompt_count"] == 0


def test_manifest_with_no_seed():
    r = _run()
    m = _build_manifest(r, seed=None)
    assert m["seed"] is None


# ---------- list/show/delete/stats via DB ----------

def test_cli_list_empty(tmp_db, capsys):
    from t2v_promptgen.cli import cmd_list
    cmd_list(None)
    out = capsys.readouterr().out
    assert "no runs" in out.lower()


def test_cli_list_with_runs(tmp_db, capsys):
    from t2v_promptgen.core.persistence import save_run
    from t2v_promptgen.cli import cmd_list
    save_run(_run("aaa"))
    save_run(_run("bbb", source="rewrite"))
    cmd_list(None)
    out = capsys.readouterr().out
    assert "aaa" in out
    assert "bbb" in out
    assert "rewrite" in out


def test_cli_show_existing(tmp_db, capsys):
    from t2v_promptgen.core.persistence import save_run
    from t2v_promptgen.cli import cmd_show
    save_run(_run("aaa"))
    args = type("X", (), {"run_id": "aaa"})()
    cmd_show(args)
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "aaa"


def test_cli_show_missing_exits(tmp_db):
    from t2v_promptgen.cli import cmd_show
    args = type("X", (), {"run_id": "nope"})()
    with pytest.raises(SystemExit):
        cmd_show(args)


def test_cli_delete(tmp_db, capsys):
    from t2v_promptgen.core.persistence import save_run, load_run
    from t2v_promptgen.cli import cmd_delete
    save_run(_run("aaa"))
    args = type("X", (), {"run_id": "aaa"})()
    cmd_delete(args)
    assert load_run("aaa") is None


def test_cli_stats(tmp_db, capsys):
    from t2v_promptgen.cli import cmd_stats
    cmd_stats(None)
    out = json.loads(capsys.readouterr().out)
    assert "total_runs" in out
    assert "by_source" in out
    assert "db_path" in out
