"""End-to-end smoke tests for the new UI-supporting endpoints
added in the UI revamp round:
  - /rewrite/{id}/decide_bulk         (bulk accept/reject)
  - /runs/{id}/p4/drop_bulk           (bulk delete prompts)
  - /runs/{id}/budget                  (raise / lower cost cap mid-run)
  - /api/runs/{id}/progress            (poll for generating page)
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from t2v_promptgen.core.schema import Phase, PromptEntry, Run
from t2v_promptgen.web.app import RUNS, app


@pytest.fixture
def client():
    return TestClient(app)


def _make_run(rid="ui1", source="generate", n_prompts=3) -> Run:
    now = datetime.now()
    run = Run(
        id=rid, capability_slug="test_cap",
        capability_display_name="测试能力",
        created_at=now, updated_at=now,
        phase=Phase.P4_REVIEW,
        source=source,
        cost_usd_limit=5.0,
        cost_usd_used=1.23,
    )
    for i in range(n_prompts):
        run.prompts.append(PromptEntry(
            id=f"rw_p{i}" if source == "rewrite" else f"p{i}",
            source_id=f"src_{i}" if source == "rewrite" else None,
            capability="test_cap", capability_version=1,
            difficulty="medium", difficulty_score=5.0,
            sl2_covered=[], axes_values={},
            subject_count=1, action_count=1,
            camera_zh=None, camera_en=None,
            prompt_zh=f"中文 prompt {i}",
            prompt_en=f"english prompt {i}",
            generated_at=now,
        ))
    RUNS[rid] = run
    return run


def teardown_function():
    for k in list(RUNS):
        if k.startswith("ui"):
            RUNS.pop(k, None)


# ---------------------------------------------------------------------------
# Bulk accept/reject (rewrite mode)
# ---------------------------------------------------------------------------

def test_rewrite_decide_bulk_accept_all(client):
    run = _make_run("ui_bulk1", source="rewrite", n_prompts=5)
    r = client.post(f"/rewrite/{run.id}/decide_bulk", json={"decision": "accept"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["updated"] == 5
    for p in run.prompts:
        assert p.rewrite_accepted is True


def test_rewrite_decide_bulk_only_subset(client):
    run = _make_run("ui_bulk2", source="rewrite", n_prompts=5)
    pick = [run.prompts[0].id, run.prompts[2].id]
    r = client.post(f"/rewrite/{run.id}/decide_bulk",
                     json={"decision": "reject", "ids": pick})
    assert r.json()["updated"] == 2
    assert run.prompts[0].rewrite_accepted is False
    assert run.prompts[1].rewrite_accepted is None
    assert run.prompts[2].rewrite_accepted is False


def test_rewrite_decide_bulk_rejects_non_rewrite_run(client):
    run = _make_run("ui_bulk3", source="generate", n_prompts=2)
    r = client.post(f"/rewrite/{run.id}/decide_bulk", json={"decision": "accept"})
    assert r.status_code == 400


def test_rewrite_decide_bulk_bad_decision(client):
    run = _make_run("ui_bulk4", source="rewrite", n_prompts=2)
    r = client.post(f"/rewrite/{run.id}/decide_bulk", json={"decision": "garbage"})
    assert r.status_code == 400
    assert r.json()["code"] == "BAD_DECISION"


def test_rewrite_decide_bulk_unset(client):
    run = _make_run("ui_bulk5", source="rewrite", n_prompts=3)
    for p in run.prompts:
        p.rewrite_accepted = True
    r = client.post(f"/rewrite/{run.id}/decide_bulk", json={"decision": "unset"})
    assert r.status_code == 200
    for p in run.prompts:
        assert p.rewrite_accepted is None


# ---------------------------------------------------------------------------
# Bulk delete (generate mode)
# ---------------------------------------------------------------------------

def test_p4_drop_bulk_removes_selected(client):
    run = _make_run("ui_drop1", source="generate", n_prompts=5)
    pick = [run.prompts[1].id, run.prompts[3].id]
    r = client.post(f"/runs/{run.id}/p4/drop_bulk", json={"ids": pick})
    assert r.status_code == 200
    assert r.json()["deleted"] == 2
    assert len(run.prompts) == 3
    assert all(p.id not in pick for p in run.prompts)


def test_p4_drop_bulk_empty_ids_is_noop(client):
    run = _make_run("ui_drop2", source="generate", n_prompts=3)
    r = client.post(f"/runs/{run.id}/p4/drop_bulk", json={"ids": []})
    assert r.json()["deleted"] == 0
    assert len(run.prompts) == 3


def test_p4_drop_bulk_unknown_ids_silent(client):
    run = _make_run("ui_drop3", source="generate", n_prompts=3)
    r = client.post(f"/runs/{run.id}/p4/drop_bulk",
                     json={"ids": ["nonexistent"]})
    assert r.json()["deleted"] == 0
    assert len(run.prompts) == 3


def test_p4_drop_bulk_rejects_non_list(client):
    run = _make_run("ui_drop4", source="generate", n_prompts=2)
    r = client.post(f"/runs/{run.id}/p4/drop_bulk", json={"ids": "not-a-list"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Budget update
# ---------------------------------------------------------------------------

def test_update_budget_raises_limit(client):
    run = _make_run("ui_b1")
    assert run.cost_usd_limit == 5.0
    r = client.post(f"/runs/{run.id}/budget", json={"limit": 25.0})
    assert r.status_code == 200
    body = r.json()
    assert body["new_limit"] == 25.0
    assert body["old_limit"] == 5.0
    assert run.cost_usd_limit == 25.0


def test_update_budget_zero_disables_cap(client):
    run = _make_run("ui_b2")
    r = client.post(f"/runs/{run.id}/budget", json={"limit": 0})
    assert r.status_code == 200
    assert run.cost_usd_limit == 0.0


def test_update_budget_rejects_negative(client):
    run = _make_run("ui_b3")
    r = client.post(f"/runs/{run.id}/budget", json={"limit": -1})
    assert r.status_code == 400
    assert r.json()["code"] == "NEGATIVE"
    assert run.cost_usd_limit == 5.0    # unchanged


def test_update_budget_bad_json(client):
    run = _make_run("ui_b4")
    r = client.post(f"/runs/{run.id}/budget", data="not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Progress API
# ---------------------------------------------------------------------------

def test_progress_api_returns_cost_and_phase(client):
    run = _make_run("ui_pr1")
    r = client.get(f"/api/runs/{run.id}/progress")
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "P4_REVIEW"
    assert body["cost_usd_used"] == 1.23
    assert body["cost_usd_limit"] == 5.0
    assert abs(body["cost_pct"] - 24.6) < 0.1
    assert body["prompts_count"] == 3


def test_progress_api_unlimited_pct_zero(client):
    run = _make_run("ui_pr2")
    run.cost_usd_limit = 0
    r = client.get(f"/api/runs/{run.id}/progress")
    assert r.json()["cost_pct"] == 0


def test_progress_api_unknown_run_404(client):
    r = client.get("/api/runs/does_not_exist/progress")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Run.capability_display_name carries through
# ---------------------------------------------------------------------------

def test_run_carries_display_name(client):
    run = _make_run("ui_dn1")
    assert run.capability_display_name == "测试能力"
    # JSON-roundtrip preserves it
    j = run.model_dump_json()
    assert "测试能力" in j


# ---------------------------------------------------------------------------
# UI revamp review-round regressions
# ---------------------------------------------------------------------------

def test_review_page_renders_with_xss_unsafe_prompt(client):
    """Audit P0: review template embedded prompt text via Jinja tojson into
    an HTML attribute (x-show). A prompt containing a double-quote would
    break out of the attribute. The fix moves text to a JS-side ROW_DATA
    map. Render a prompt with " and \\ and verify nothing leaks into an
    attribute value that would be syntactically broken."""
    from datetime import datetime
    run = Run(
        id="ui_xss1", capability_slug="cap",
        capability_display_name="cap",
        created_at=datetime.now(), updated_at=datetime.now(),
        phase=Phase.P4_REVIEW, source="rewrite",
    )
    run.prompts.append(PromptEntry(
        id="rw_xss",
        source_id='src" /><script>alert(1)</script><div data-x="',
        capability="cap", capability_version=1,
        difficulty="medium", difficulty_score=5.0,
        sl2_covered=[], axes_values={},
        subject_count=1, action_count=1,
        camera_zh=None, camera_en=None,
        prompt_zh='含"双引号"and\\back\\slash and <script>',
        prompt_en='english with " quote',
        generated_at=datetime.now(),
    ))
    RUNS["ui_xss1"] = run
    try:
        r = client.get("/runs/ui_xss1")
        assert r.status_code == 200
        body = r.text
        # The unescaped attack string must NOT appear verbatim as raw HTML
        # injection (script tag from prompt text shouldn't be parseable).
        # tojson encodes it as a JS string literal — that's safe inside
        # <script>...</script> but NOT inside an attribute. Verify it's
        # inside a script block, not an attribute.
        # Cheap check: the script tag from the prompt text must be escaped
        # (no literal "<script>alert(1)" outside of safe-escape form).
        # Jinja's tojson uses unicode escapes for `<` and `>`.
        assert "<script>alert(1)" not in body
        # The script-rendered ROW_DATA must include the pid as a JS key
        assert '"rw_xss"' in body
    finally:
        RUNS.pop("ui_xss1", None)


def test_progress_api_unknown_run_404(client):
    # Already exists above but keeping a marker to verify ordering didn't shift
    r = client.get("/api/runs/_unknown_/progress")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Pre-assignment preview + reroll seed endpoints
# ---------------------------------------------------------------------------

def _make_rewrite_run_with_sources(rid="ui_assign1", n=6):
    """A minimal rewrite run with N source prompts ready for preview."""
    from datetime import datetime
    from t2v_promptgen.core.rewrite_schema import SourcePrompt
    run = Run(
        id=rid, capability_slug="custom_rewrite",
        capability_display_name="改写已有 prompt",
        created_at=datetime.now(), updated_at=datetime.now(),
        phase=Phase.P2_PROMPTS, source="rewrite",
        cost_usd_limit=5.0,
    )
    run.source_prompts = [
        SourcePrompt(source_id=f"s{i}", original_text=f"原 prompt {i}",
                      selected=True, failed_to_rewrite=False)
        for i in range(n)
    ]
    RUNS[rid] = run
    return run


def test_preview_assignment_balanced(client):
    run = _make_rewrite_run_with_sources("ui_pa1", n=9)
    body = {
        "transforms": [{
            "id": "scene_shift",
            "params": {"target_scene": ["E1 室外自然", "E5 室内现代", "E8 奇幻虚拟"],
                       "preserve_action": "是"},
            "order": 0,
        }],
        "seed": 42,
    }
    r = client.post(f"/rewrite/{run.id}/preview_assignment", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["seed"] == 42
    assert d["total"] == 9
    counts = d["per_card"]["scene_shift"]["target_scene"]
    assert sum(counts.values()) == 9
    # 9 / 3 = 3 exact each
    assert all(c == 3 for c in counts.values())


def test_preview_assignment_single_pick_no_buckets(client):
    """Single-pick → no per-prompt assignment needed → per_card empty."""
    run = _make_rewrite_run_with_sources("ui_pa2", n=5)
    body = {"transforms": [{
        "id": "scene_shift",
        "params": {"target_scene": ["E1 室外自然"], "preserve_action": "是"},
        "order": 0,
    }]}
    r = client.post(f"/rewrite/{run.id}/preview_assignment", json=body)
    d = r.json()
    assert d["per_card"] == {}    # nothing to spread


def test_preview_assignment_rejects_non_rewrite(client):
    """Generate-mode run can't use the rewrite preview endpoint."""
    run = _make_run("ui_pa3", source="generate")
    r = client.post(f"/runs/{run.id}/preview_assignment", json={})
    assert r.status_code == 404 or r.status_code == 405    # route is /rewrite/...
    r = client.post(f"/rewrite/{run.id}/preview_assignment", json={})
    assert r.status_code == 400


def test_preview_assignment_no_transforms_returns_empty(client):
    run = _make_rewrite_run_with_sources("ui_pa4", n=3)
    r = client.post(f"/rewrite/{run.id}/preview_assignment",
                     json={"transforms": [], "seed": 1})
    d = r.json()
    assert d["ok"] is True
    assert d["per_card"] == {}


def test_seed_reroll_persists_new_seed(client):
    run = _make_rewrite_run_with_sources("ui_pa5", n=3)
    run.rewrite_seed = 100
    r = client.post(f"/rewrite/{run.id}/seed", json={})
    d = r.json()
    assert d["ok"] is True
    assert d["seed"] != 100    # must have changed
    assert run.rewrite_seed == d["seed"]


def test_seed_explicit_value_accepted(client):
    run = _make_rewrite_run_with_sources("ui_pa6", n=3)
    r = client.post(f"/rewrite/{run.id}/seed", json={"seed": 12345})
    assert r.json()["seed"] == 12345
    assert run.rewrite_seed == 12345


def test_seed_rejects_garbage(client):
    run = _make_rewrite_run_with_sources("ui_pa7", n=3)
    r = client.post(f"/rewrite/{run.id}/seed", json={"seed": "abc"})
    assert r.status_code == 400


def test_goto_phase_rewrite_blocks_p1_jump(client):
    """Audit P1-1: rewrite runs cannot rewind to P1/P0 (would orphan
    source_prompts). Must return 400 with a clear message."""
    run = _make_rewrite_run_with_sources("ui_goto1", n=2)
    run.phase = Phase.P4_REVIEW
    r = client.post(f"/runs/{run.id}/goto/P1_DIMENSIONS")
    assert r.status_code == 400
    assert "字段映射" in r.json().get("detail", "")
    # Run state is intact — phase not changed, source_prompts not touched
    assert run.phase == Phase.P4_REVIEW
    assert len(run.source_prompts) == 2


def test_goto_phase_rewrite_allows_p5_to_p4(client):
    """Going back from export to review on a rewrite run is fine —
    no destructive side-effects."""
    run = _make_rewrite_run_with_sources("ui_goto2", n=2)
    run.phase = Phase.P5_EXPORT
    r = client.post(f"/runs/{run.id}/goto/P4_REVIEW")
    assert r.status_code in (200, 303)
    assert run.phase == Phase.P4_REVIEW


def test_clone_rewrite_blocked(client):
    """Audit P1-5: cloning a rewrite run would orphan a new Run with no
    source_file. Must be blocked with 400, not silently create the orphan."""
    run = _make_rewrite_run_with_sources("ui_clone1", n=2)
    before = set(RUNS.keys())
    r = client.post(f"/runs/{run.id}/clone")
    assert r.status_code == 400
    # No new run was created
    assert set(RUNS.keys()) == before


def test_clone_generate_skips_rewrite_fields(client):
    """Audit P1-5 (other direction): a generate-mode clone must not
    carry rewrite_directive / rewrite_seed (they'd be structural mismatch)."""
    from datetime import datetime
    src = Run(
        id="ui_clone2", capability_slug="cap",
        capability_display_name="cap",
        created_at=datetime.now(), updated_at=datetime.now(),
        phase=Phase.P4_REVIEW, source="generate",
        cost_usd_limit=10.0, cost_usd_used=3.5,
    )
    RUNS["ui_clone2"] = src
    r = client.post("/runs/ui_clone2/clone")
    assert r.status_code in (200, 303)
    # Find the new run id from the response
    new_ids = [k for k in RUNS if k != "ui_clone2"]
    new_id = next((k for k in new_ids if RUNS[k].user_description and
                    "ui_clone2" in (RUNS[k].user_description or "")), None)
    assert new_id is not None
    clone = RUNS[new_id]
    assert clone.source == "generate"
    assert clone.rewrite_directive is None
    assert clone.cost_usd_limit == 10.0
    assert clone.cost_usd_used == 0.0     # reset
    # Cleanup
    RUNS.pop(new_id, None)


def test_coverage_8d_download(client):
    """Audit P1-14: dedicated download endpoint for the 8-dim coverage
    report (was previously only viewable in the review page UI)."""
    run = _make_run("ui_8d1", source="generate", n_prompts=3)
    r = client.get(f"/runs/{run.id}/download/coverage_8d.json")
    assert r.status_code == 200
    d = r.json()
    assert "dims" in d
    assert len(d["dims"]) == 8
    assert d["total_prompts"] == 3
    cd = r.headers.get("content-disposition", "")
    assert "coverage_8d" in cd


def test_autopersist_clears_last_error_on_success(client):
    """Audit S3: a successful 2xx mutation clears RUN_LAST_ERROR so
    stale 'budget exceeded' banners don't haunt the UI forever.

    Run id must be hex-only — the autopersist middleware regex extracts
    `[a-f0-9]+` to identify the run on the URL path; non-hex ids would
    silently skip persistence (matches real UUID-based ids).
    """
    from t2v_promptgen.web.app import RUN_LAST_ERROR
    run = _make_run("abc12345", source="generate")
    RUN_LAST_ERROR[run.id] = "[预算已用满] some old failure"
    try:
        # A 2xx mutation should clear it via the autopersist middleware
        r = client.post(f"/runs/{run.id}/budget", json={"limit": 20.0})
        assert r.status_code == 200
        assert run.id not in RUN_LAST_ERROR
    finally:
        RUNS.pop("abc12345", None)
        RUN_LAST_ERROR.pop("abc12345", None)


def test_autopersist_keeps_last_error_on_4xx(client):
    """4xx leaves the error in place so the user sees what just failed."""
    from t2v_promptgen.web.app import RUN_LAST_ERROR
    run = _make_run("abcdef12", source="generate")
    RUN_LAST_ERROR[run.id] = "[预算已用满] stale"
    try:
        # 4xx: negative limit
        r = client.post(f"/runs/{run.id}/budget", json={"limit": -5})
        assert r.status_code == 400
        assert RUN_LAST_ERROR.get(run.id) == "[预算已用满] stale"
    finally:
        RUNS.pop("abcdef12", None)
        RUN_LAST_ERROR.pop("abcdef12", None)


def test_preview_changes_with_different_seed(client):
    """Same directive, different seed → same totals but different per-prompt
    distribution (verified by checking seed echoes back)."""
    run = _make_rewrite_run_with_sources("ui_pa8", n=6)
    body = {"transforms": [{
        "id": "scene_shift",
        "params": {"target_scene": ["E1 室外自然", "E5 室内现代"],
                   "preserve_action": "是"},
        "order": 0,
    }]}
    r1 = client.post(f"/rewrite/{run.id}/preview_assignment",
                      json={**body, "seed": 1}).json()
    r2 = client.post(f"/rewrite/{run.id}/preview_assignment",
                      json={**body, "seed": 2}).json()
    assert r1["seed"] != r2["seed"]
    # Totals still balanced
    for r in (r1, r2):
        c = r["per_card"]["scene_shift"]["target_scene"]
        assert c == {"E1 室外自然": 3, "E5 室内现代": 3}
