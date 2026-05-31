"""FastAPI app for t2v_promptgen web UI.

Run:
    pip install fastapi uvicorn jinja2 python-multipart pydantic pyyaml
    uvicorn t2v_promptgen.web.app:app --reload --port 8000

Then open http://localhost:8000

Prototype scope:
    - Single-user, in-memory state (no DB yet)
    - All LLM calls mocked via web.mock_data
    - End-to-end clickable flow P0 → P5
    - Designed so swapping in real orchestrator is a small change
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..core.schema import Axis, Phase, PromptEntry, Run, SL2
from . import mock_data
from . import llm_phases
from .llm_routes import router as llm_router

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent
app = FastAPI(title="t2v_promptgen", version="0.7")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
app.include_router(llm_router)

# In-memory store
RUNS: dict[str, Run] = {}

# Per-run credentials (never persisted, never returned via API)
RUN_CREDS: dict[str, dict] = {}


def _try_real_dimensions(run_id: str, run: Run, feedback: str = ""):
    """Try LLM-backed dimension generation. Returns (sl2, axes) or raises."""
    creds = RUN_CREDS.get(run_id)
    if not creds or not creds.get("api_key"):
        raise RuntimeError("no credentials")
    client = llm_phases.build_client(
        provider=creds["provider"],
        model=creds.get("model_p1") or creds["model"],   # higher-quality model for P1
        api_key=creds["api_key"],
        base_url=creds.get("base_url") or None,
    )
    return llm_phases.generate_dimensions_real(
        description=run.user_description or "",
        client=client,
        previous_sl2=run.sl2_list or None,
        previous_axes=run.axes or None,
        feedback=feedback,
        round_idx=run.p1_round,
    )


def _try_judge_dimensions(run_id: str, run: Run):
    """Judge the current dimensions design. Returns critique dict (always)."""
    import time
    from ..qa.dimensions_judge import judge_dimensions
    creds = RUN_CREDS.get(run_id)
    client = None
    if creds and creds.get("api_key"):
        try:
            client = llm_phases.build_client(
                provider=creds["provider"],
                model=creds.get("model_p2") or creds["model"],   # use cheap model for judge
                api_key=creds["api_key"],
                base_url=creds.get("base_url") or None,
            )
        except Exception as exc:
            print(f"[P1 judge client build failed] {exc}", flush=True)
    try:
        t0 = time.time()
        critique = judge_dimensions(
            description=run.user_description or "",
            sl2_list=run.sl2_list,
            axes=run.axes,
            client=client,
        )
        print(f"[LLM-timing] dim_judge done in {time.time()-t0:.1f}s  run_id={run_id}", flush=True)
        return critique.to_dict()
    except Exception as exc:
        print(f"[P1 judge failed] {exc}", flush=True)
        return {"judge_ran": False}


def _run_dim_judge_background(run_id: str):
    """Run dim judge in background, store result in RUN_DIM_CRITIQUE.

    Called by BackgroundTasks after POST /runs / regenerate returns to user.
    """
    run = RUNS.get(run_id)
    if not run:
        return
    # Mark as pending so UI knows to show spinner
    RUN_DIM_CRITIQUE[run_id] = {"pending": True}
    RUN_DIM_CRITIQUE[run_id] = _try_judge_dimensions(run_id, run)


def _try_real_prompts(run_id: str, run: Run):
    """Try LLM-backed prompt generation. Returns list[PromptEntry] or raises."""
    creds = RUN_CREDS.get(run_id)
    if not creds or not creds.get("api_key"):
        raise RuntimeError("no credentials")
    client = llm_phases.build_client(
        provider=creds["provider"],
        model=creds.get("model_p2") or creds["model"],   # cheaper/faster model for P2
        api_key=creds["api_key"],
        base_url=creds.get("base_url") or None,
    )
    return llm_phases.generate_prompts_real(
        capability=run.capability_slug,
        sl2_list=run.sl2_list,
        axes=run.axes,
        target_size=run.target_set_size or 40,
        client=client,
    )


def _try_real_qa(run_id: str, run: Run):
    """Try LLM-backed QA. Returns QAReport (rules-only report if no creds).

    Always mutates run.prompts in place by populating qa_* fields. The phase 3
    function is tolerant — if the client errors mid-batch, scores stay None
    and prompts default to "passed by rules".
    """
    creds = RUN_CREDS.get(run_id)
    client = None
    if creds and creds.get("api_key"):
        # Use the P2 (cheaper) model for judges — they're simple classifiers
        client = llm_phases.build_client(
            provider=creds["provider"],
            model=creds.get("model_p2") or creds["model"],
            api_key=creds["api_key"],
            base_url=creds.get("base_url") or None,
        )
    return llm_phases.run_qa_real(
        prompts=run.prompts,
        sl2_list=run.sl2_list,
        axes=run.axes,
        client=client,
    )


# Run-level QA reports (in-memory, not persisted)
RUN_QA_REPORTS: dict[str, dict] = {}

# Last error per run (surfaced in dimensions/review pages so silent fallbacks are visible)
RUN_LAST_ERROR: dict[str, str] = {}

# P0 intake classification result (slug + confidence + reasoning) per run
RUN_INTAKE: dict[str, dict] = {}

# P1 dimensions critique per run (score, verdict, sl2 issues, axes issues, gaps)
RUN_DIM_CRITIQUE: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHASE_TEMPLATE = {
    Phase.P0_INTAKE: "intake.html",
    Phase.P1_DIMENSIONS: "dimensions.html",
    Phase.P2_PROMPTS: "generating.html",
    Phase.P3_QA: "generating.html",
    Phase.P4_REVIEW: "review.html",
    Phase.P5_EXPORT: "export.html",
    Phase.DONE: "export.html",
}

PHASE_ORDER = [
    Phase.P0_INTAKE,
    Phase.P1_DIMENSIONS,
    Phase.P2_PROMPTS,
    Phase.P3_QA,
    Phase.P4_REVIEW,
    Phase.P5_EXPORT,
]

PHASE_LABEL_ZH = {
    Phase.P0_INTAKE: "理解需求",
    Phase.P1_DIMENSIONS: "确定评测维度",
    Phase.P2_PROMPTS: "生成测试用例",
    Phase.P3_QA: "自动质检",
    Phase.P4_REVIEW: "审核确认",
    Phase.P5_EXPORT: "导出结果",
    Phase.DONE: "完成",
}

# P2-6: rewrite-mode tracker labels (R0-R6 maps internally onto P0-P5)
PHASE_LABEL_REWRITE = {
    Phase.P0_INTAKE: "上传文件",
    Phase.P1_DIMENSIONS: "字段映射",
    Phase.P2_PROMPTS: "改写指令",
    Phase.P3_QA: "改写中",
    Phase.P4_REVIEW: "审核确认",
    Phase.P5_EXPORT: "导出结果",
    Phase.DONE: "完成",
}


def _ctx(run: Run, **extra) -> dict:
    """Build common template context.

    P2-6: pick rewrite-mode phase labels when run.source == 'rewrite'.
    """
    labels = PHASE_LABEL_REWRITE if run.source == "rewrite" else PHASE_LABEL_ZH
    return {
        "run": run,
        "phase_order": PHASE_ORDER,
        "phase_label": labels,
        **extra,
    }


def _get_run(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    return run


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    runs_sorted = sorted(RUNS.values(), key=lambda r: r.updated_at, reverse=True)
    return templates.TemplateResponse(request, "index.html", {
        "runs": runs_sorted,
        "phase_label": PHASE_LABEL_ZH,
        "phase_label_rewrite": PHASE_LABEL_REWRITE,
    })


# ---------------------------------------------------------------------------
# Create + view run
# ---------------------------------------------------------------------------

@app.post("/runs")
def create_run(
    background_tasks: BackgroundTasks,
    description: str = Form(...),
    set_size: str = Form("auto"),
    provider: str = Form("deepseek"),
    model_p1: str = Form("deepseek-v4-pro"),       # 维度生成(质量,慢)
    model_p2: str = Form("deepseek-chat"),         # prompt 生成(速度,多批次)
    api_key: str = Form(""),
    base_url: str = Form(""),
):
    run_id = str(uuid.uuid4())[:8]
    now = datetime.now()

    # P0: LLM-driven capability classification (with keyword fallback)
    intake_client = None
    if api_key:
        try:
            intake_client = llm_phases.build_client(
                provider=provider,
                model=model_p2,            # use fast model — intake is just classification
                api_key=api_key,
                base_url=base_url or None,
            )
        except Exception as exc:
            print(f"[P0 client build failed] {type(exc).__name__}: {exc}", flush=True)
            intake_client = None

    from ..phases.intake import classify_with_fallback
    import time
    _t0 = time.time()
    intake = classify_with_fallback(description, client=intake_client)
    slug = intake["slug"]
    print(f"[LLM-timing] intake done in {time.time()-_t0:.1f}s  "
          f"slug={slug} confidence={intake['confidence']} source={intake.get('source')}",
          flush=True)
    RUN_INTAKE[run_id] = intake

    run = Run(
        id=run_id,
        capability_slug=slug,
        created_at=now,
        updated_at=now,
        phase=Phase.P1_DIMENSIONS,             # auto-skip P0 (intake is just slug extraction)
        user_description=description,
        provider=provider,
        model=f"{model_p1} / {model_p2}",        # display-only
    )

    # Store credentials for this run (memory only)
    if api_key:
        RUN_CREDS[run_id] = {
            "provider": provider,
            "model": model_p1,                   # fallback if a phase doesn't specify
            "model_p1": model_p1,
            "model_p2": model_p2,
            "api_key": api_key,
            "base_url": base_url or None,
        }

    # Try real LLM, fall back to mock — log loudly so silent failures are visible
    try:
        _t0 = time.time()
        run.sl2_list, run.axes, run.recommended_tags = _try_real_dimensions(run_id, run)
        print(f"[LLM-timing] dimensions done in {time.time()-_t0:.1f}s  "
              f"sl2={len(run.sl2_list)} axes={len(run.axes)} "
              f"tags={sum(len(v) for v in run.recommended_tags.values())}", flush=True)
        # If LLM didn't return any tag recommendations, use slug-based defaults
        if not run.recommended_tags:
            run.recommended_tags = mock_data.default_recommended_tags(slug)
        run.original_ai_tags = {k: list(v) for k, v in run.recommended_tags.items()}
    except Exception as exc:
        import traceback
        err = f"[P1 LLM 调用失败 → 走 mock] slug={slug}  {type(exc).__name__}: {exc}"
        print(err, flush=True)
        traceback.print_exc()
        RUN_LAST_ERROR[run_id] = err
        run.sl2_list, run.axes = mock_data.generate_mock_dimensions(
            description, round=0, capability_slug=slug
        )
        run.recommended_tags = mock_data.default_recommended_tags(slug)
        run.original_ai_tags = {k: list(v) for k, v in run.recommended_tags.items()}

    # Compute target set size from axes (decision C3)
    from ..phases.dimensions import compute_min_set_size
    if set_size == "auto":
        run.target_set_size = compute_min_set_size(run.axes)
    else:
        try:
            run.target_set_size = max(40, min(120, int(set_size)))
        except ValueError:
            run.target_set_size = 60

    RUNS[run_id] = run

    # P1 judge runs in background — don't block redirect (saves ~10-15s).
    # UI will show "评审中..." until result lands.
    RUN_DIM_CRITIQUE[run_id] = {"pending": True}
    background_tasks.add_task(_run_dim_judge_background, run_id)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def view_run(request: Request, run_id: str):
    run = _get_run(run_id)
    template = PHASE_TEMPLATE[run.phase]

    extra = {}
    if run.phase == Phase.P4_REVIEW:
        from .mock_data import compute_coverage_matrix
        extra["coverage"] = compute_coverage_matrix(run.prompts, run.sl2_list, run.axes)
        extra["qa_report"] = RUN_QA_REPORTS.get(run_id, {})
        # For rewrite runs, build {prompt_id: SourcePrompt} for diff view
        if run.source == "rewrite":
            sp_by_id = {sp.source_id: sp for sp in run.source_prompts}
            extra["source_by_pid"] = {
                p.id: sp_by_id.get(p.source_id) for p in run.prompts
            }

    if RUN_LAST_ERROR.get(run_id):
        extra["last_error"] = RUN_LAST_ERROR[run_id]

    if RUN_INTAKE.get(run_id):
        extra["intake"] = RUN_INTAKE[run_id]

    if run.phase == Phase.P1_DIMENSIONS and RUN_DIM_CRITIQUE.get(run_id):
        extra["dim_critique"] = RUN_DIM_CRITIQUE[run_id]

    if run.phase == Phase.P1_DIMENSIONS:
        from ..core.annotation_schema import ALL_DIMENSIONS
        extra["all_dimensions"] = ALL_DIMENSIONS

    return templates.TemplateResponse(request, template, _ctx(run, **extra))


@app.post("/runs/{run_id}/goto/{target}")
def goto_phase(run_id: str, target: str):
    """Jump back to an earlier phase, clearing forward state.

    Going back to:
    - P1_DIMENSIONS: keep slug + sl2_list + axes (user can edit on dim page),
      clear prompts + QA — they'll be regenerated.
    - P2_PROMPTS: same as P1 — there's no standalone P2 page, so this lands
      on the dimensions confirm flow.
    - P3_QA: keep prompts, clear QA reports — re-runs P3 immediately.
    - P4_REVIEW: keep everything (from P5 only).
    """
    run = _get_run(run_id)
    try:
        target_phase = Phase(target)
    except ValueError:
        raise HTTPException(400, f"Unknown phase: {target}")

    current_idx = PHASE_ORDER.index(run.phase) if run.phase in PHASE_ORDER else 0
    try:
        target_idx = PHASE_ORDER.index(target_phase)
    except ValueError:
        raise HTTPException(400, f"Phase {target} not in PHASE_ORDER")

    if target_idx > current_idx:
        raise HTTPException(400, "Can only go back, not forward")
    if target_idx == current_idx:
        # No-op, but harmless
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    # Discard forward state based on how far we're going back
    if target_idx <= PHASE_ORDER.index(Phase.P1_DIMENSIONS):
        # Going back to P1 or earlier — discard prompts + qa
        run.prompts = []
        RUN_QA_REPORTS.pop(run_id, None)
        run.p4_round = 0
    if target_idx <= PHASE_ORDER.index(Phase.P3_QA):
        # Going back to P3 or earlier — discard QA results from each prompt
        for p in run.prompts:
            p.qa_rule_errors = []
            p.qa_naturalness_zh = None
            p.qa_naturalness_en = None
            p.qa_naturalness_issues = []
            p.qa_judged_sl2 = []
            p.qa_coverage_match = None
            p.qa_passed = True
            p.needs_human_review = False
        RUN_QA_REPORTS.pop(run_id, None)

    # Special case: targeting P3 directly = re-run QA, then jump straight to P4
    # (P3 has no standalone UI — generating.html flashes by)
    if target_phase == Phase.P3_QA and run.prompts:
        run.phase = Phase.P3_QA
        try:
            report = _try_real_qa(run_id, run)
            RUN_QA_REPORTS[run_id] = {
                "total": report.total, "passed": report.passed,
                "pass_rate": report.pass_rate,
                "fail_rules": report.fail_rules,
                "fail_naturalness": report.fail_naturalness,
                "fail_coverage": report.fail_coverage,
                "naturalness_zh_avg": round(report.naturalness_zh_avg, 1),
                "naturalness_en_avg": round(report.naturalness_en_avg, 1),
                "stress_ratio": round(report.stress_ratio, 2),
                "sl2_uncovered": report.sl2_uncovered,
                "judges_ran": report.judges_ran,
            }
        except Exception as e:
            RUN_QA_REPORTS[run_id] = {"error": str(e), "judges_ran": False}
        run.phase = Phase.P4_REVIEW
    else:
        run.phase = target_phase

    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/tags/toggle")
async def toggle_tag(run_id: str, dim: str = Form(...), code: str = Form(...)):
    """Toggle a tag in recommended_tags. Returns JSON for AJAX use."""
    from ..core.annotation_schema import CODE_INDEX
    run = _get_run(run_id)
    # Allow custom codes (not in CODE_INDEX) — they're stored in run.custom_tags
    if code in CODE_INDEX:
        actual_dim = CODE_INDEX[code][0].code
        if actual_dim != dim:
            return JSONResponse(
                {"ok": False, "error": f"Code {code} belongs to {actual_dim}, not {dim}"},
                status_code=400,
            )
    cur = run.recommended_tags.setdefault(dim, [])
    if code in cur:
        cur.remove(code)
        selected = False
        if not cur:
            del run.recommended_tags[dim]
    else:
        cur.append(code)
        selected = True
    run.updated_at = datetime.now()
    return JSONResponse({"ok": True, "dim": dim, "code": code, "selected": selected,
                         "all_selected": run.recommended_tags.get(dim, [])})


@app.post("/runs/{run_id}/tags/custom")
async def add_custom_tag(run_id: str, dim: str = Form(...), name_zh: str = Form(...)):
    """Add a user-defined tag for this run only. Returns JSON for AJAX use."""
    from ..core.annotation_schema import ALL_DIMENSIONS
    run = _get_run(run_id)
    dim_obj = next((d for d in ALL_DIMENSIONS if d.code == dim), None)
    if not dim_obj:
        return JSONResponse({"ok": False, "error": f"Unknown dimension: {dim}"}, status_code=400)
    name_zh = (name_zh or "").strip()
    if not name_zh:
        return JSONResponse({"ok": False, "error": "Empty tag name"}, status_code=400)
    cur = run.custom_tags.setdefault(dim, [])
    custom_code = f"{dim_obj.prefix}X{len(cur)+1}"
    cur.append({"code": custom_code, "name_zh": name_zh})
    run.recommended_tags.setdefault(dim, []).append(custom_code)
    run.updated_at = datetime.now()
    return JSONResponse({
        "ok": True, "dim": dim, "code": custom_code, "name_zh": name_zh,
        "all_selected": run.recommended_tags.get(dim, []),
        "all_customs": run.custom_tags.get(dim, []),
    })


# ===========================================================================
# Rewrite feature — R0 upload, R1 field mapping (PR-1)
# ===========================================================================
# Stores parsed raw rows alongside the run so R1 can populate after mapping.
RUN_RAW_ROWS: dict[str, list[dict]] = {}


@app.get("/rewrite/upload", response_class=HTMLResponse)
def rewrite_upload_page(request: Request):
    """R0: dedicated upload page (linked from index tab)."""
    return templates.TemplateResponse(request, "rewrite_upload.html", {})


@app.post("/rewrite/upload")
def rewrite_upload(
    file: UploadFile = File(...),
    sheet_name: str = Form(""),
    provider: str = Form("deepseek"),
    model_p1: str = Form("deepseek-chat"),
    model_p2: str = Form("deepseek-chat"),
    api_key: str = Form(""),
    base_url: str = Form(""),
):
    """R0: parse uploaded prompt list, create a run, redirect to R1."""
    from ..parsers.prompt_loader import load_prompts
    from ..core.rewrite_schema import ParseError

    raw = file.file.read()
    try:
        source_file, rows = load_prompts(
            raw, file.filename or "uploaded",
            sheet_name=sheet_name or None,
        )
    except ParseError as exc:
        # Return a JSON error so the upload form can show it inline
        return JSONResponse(
            {"ok": False, "code": exc.code, "message": str(exc),
             "location": exc.location},
            status_code=413 if exc.code in ("SIZE_EXCEEDED", "ROW_EXCEEDED") else 400,
        )

    # Build a new rewrite run
    run_id = str(uuid.uuid4())[:8]
    now = datetime.now()
    run = Run(
        id=run_id,
        capability_slug="custom_rewrite",
        created_at=now, updated_at=now,
        phase=Phase.P1_DIMENSIONS,        # R1 maps onto P1 slot internally
        user_description=f"[改写任务] {source_file.filename} ({source_file.row_count} 条)",
        provider=provider,
        model=f"{model_p1} / {model_p2}",
        source="rewrite",
        source_file=source_file,
    )
    RUNS[run_id] = run
    RUN_RAW_ROWS[run_id] = rows

    if api_key:
        RUN_CREDS[run_id] = {
            "provider": provider,
            "model": model_p1,
            "model_p1": model_p1,
            "model_p2": model_p2,
            "api_key": api_key,
            "base_url": base_url or None,
        }

    return RedirectResponse(f"/rewrite/{run_id}/map", status_code=303)


@app.get("/rewrite/{run_id}/map", response_class=HTMLResponse)
def rewrite_map_page(request: Request, run_id: str):
    """R1: field-mapping page. Auto-runs LLM/heuristic guess if mapping empty."""
    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")

    raw_rows = RUN_RAW_ROWS.get(run_id, [])
    # P1-1: union of keys across rows (handle mixed JSON arrays where
    # different rows have different schemas — e.g. dicts + bare strings)
    columns: list[str] = []
    seen = set()
    for row in raw_rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                columns.append(k)

    # If mapping not set yet, try guess (LLM if creds available, else heuristic)
    suggestion_text = ""
    if not run.field_mapping:
        from ..parsers.field_mapper import llm_guess
        creds = RUN_CREDS.get(run_id)
        client = None
        if creds and creds.get("api_key"):
            try:
                client = llm_phases.build_client(
                    provider=creds["provider"],
                    model=creds.get("model_p2") or creds["model"],
                    api_key=creds["api_key"],
                    base_url=creds.get("base_url") or None,
                )
            except Exception:
                client = None
        m, suggestion_text = llm_guess(columns, raw_rows[:5], client=client)
        # Store the guess as suggestion (not yet committed)
        run.field_mapping = {
            k: v for k, v in [
                ("prompt_zh", m.prompt_zh),
                ("prompt_en", m.prompt_en),
                ("source_id", m.source_id),
            ] if v
        }

    return templates.TemplateResponse(request, "rewrite_map.html", {
        "run": run,
        "columns": columns,
        "sample_rows": raw_rows[:5],
        "current_mapping": run.field_mapping,
        "suggestion_text": suggestion_text,
        "phase_order": PHASE_ORDER,
        "phase_label": PHASE_LABEL_REWRITE,
    })


@app.post("/rewrite/{run_id}/map")
def rewrite_map_confirm(
    run_id: str,
    prompt_zh: str = Form(""),
    prompt_en: str = Form(""),
    source_id: str = Form(""),
):
    """R1 confirm: validate user's column mapping, fill SourcePrompt list."""
    from ..core.rewrite_schema import FieldMapping, SourcePrompt, ParseError

    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")

    # P1-7: prevent remapping after rewrite started — would orphan run.prompts
    if run.prompts:
        return JSONResponse(
            {"ok": False, "code": "MAPPING_LOCKED",
             "message": f"已生成 {len(run.prompts)} 条改写产物;重新映射会让它们对不上原文。"
                        "如果确实要重新映射,先删除当前任务重新开始。"},
            status_code=409,
        )

    # Build mapping (Pydantic validator requires ≥1 prompt column)
    try:
        mapping = FieldMapping(
            prompt_zh=prompt_zh.strip() or None,
            prompt_en=prompt_en.strip() or None,
            source_id=source_id.strip() or None,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "code": "MAPPING_INVALID", "message": str(exc)},
            status_code=400,
        )

    raw_rows = RUN_RAW_ROWS.get(run_id, [])
    # P1-1: union of keys across all rows
    columns: list[str] = []
    seen_cols = set()
    for row in raw_rows:
        for k in row.keys():
            if k not in seen_cols:
                seen_cols.add(k)
                columns.append(k)

    # Verify mapped column names exist in the file
    for key, val in [("prompt_zh", mapping.prompt_zh),
                     ("prompt_en", mapping.prompt_en),
                     ("source_id", mapping.source_id)]:
        if val and val not in columns:
            return JSONResponse(
                {"ok": False, "code": "MAPPING_COLUMN_NOT_FOUND",
                 "message": f"列 {val!r} 不存在,请从下拉中选"},
                status_code=400,
            )

    # Build normalized SourcePrompt records with source_id sanitization
    # (P0-2) and de-duplication (P0-3)
    source_prompts: list[SourcePrompt] = []
    seen_ids: dict[str, int] = {}      # sanitized_id → next-suffix counter
    for idx, row in enumerate(raw_rows):
        raw_sid = str(row.get(mapping.source_id, idx + 1)) if mapping.source_id else str(idx + 1)

        # P0-2: sanitize source_id — only [a-zA-Z0-9_-], everything else → _
        sid = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw_sid).strip("_")[:64] or str(idx + 1)

        # P0-3: de-duplicate (preserves order, appends _2/_3/... on collisions)
        if sid in seen_ids:
            seen_ids[sid] += 1
            sid = f"{sid}_{seen_ids[sid]}"
            # Make sure suffixed version isn't itself a dup (rare)
            while sid in seen_ids:
                seen_ids[sid.rsplit('_', 1)[0]] += 1
                sid = f"{sid.rsplit('_', 1)[0]}_{seen_ids[sid.rsplit('_', 1)[0]]}"
        seen_ids[sid] = 1

        zh = str(row.get(mapping.prompt_zh) or "").strip() if mapping.prompt_zh else ""
        en = str(row.get(mapping.prompt_en) or "").strip() if mapping.prompt_en else ""

        # Metadata = everything else (preserve original raw id for traceability)
        meta = {k: v for k, v in row.items()
                if k not in (mapping.prompt_zh, mapping.prompt_en, mapping.source_id)}
        if mapping.source_id and raw_sid != sid:
            meta["_original_source_id"] = raw_sid    # so user can join back

        text_for_source = zh or en   # at least one is required per validator
        if not text_for_source:
            # Row had empty content under both mapped columns; mark failed
            # P1-5: use empty string, not "(empty)" literal
            sp = SourcePrompt(
                source_id=sid, original_text="(empty row)",
                metadata=meta, selected=False,
                failed_to_rewrite=True,
                fail_reason="empty after mapping",
            )
        else:
            sp = SourcePrompt(
                source_id=sid,
                original_text=zh or en,
                original_text_en=en or None,
                metadata=meta,
            )
        source_prompts.append(sp)

    # Commit
    run.source_prompts = source_prompts
    run.field_mapping = {
        k: v for k, v in [
            ("prompt_zh", mapping.prompt_zh),
            ("prompt_en", mapping.prompt_en),
            ("source_id", mapping.source_id),
        ] if v
    }
    run.updated_at = datetime.now()
    return RedirectResponse(f"/rewrite/{run_id}/directive", status_code=303)


# ===========================================================================
# Rewrite R2 + R3 (PR-2)
# ===========================================================================
# Run-level mutable state — async R3 needs this for cancel + progress
RUN_REWRITE_STATE: dict[str, dict] = {}        # {run_id: {status, done, total, started_at, result}}
RUN_REWRITE_CANCEL: dict[str, bool] = {}       # {run_id: bool} — set True to cancel mid-batch
_REWRITE_STATE_LOCK = threading.Lock()         # P0-5: protect check-then-set on RUN_REWRITE_STATE


@app.get("/rewrite/cards")
def rewrite_cards_spec():
    """Return all 12 card definitions for the directive UI."""
    from ..phases.rewrite_cards import cards_to_ui_dict
    return JSONResponse(cards_to_ui_dict())


@app.get("/rewrite/{run_id}/directive", response_class=HTMLResponse)
def rewrite_directive_page(request: Request, run_id: str):
    """R2: card + free-text directive page."""
    from ..phases.rewrite_cards import cards_to_ui_dict
    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")

    eligible = [p for p in run.source_prompts if p.selected and not p.failed_to_rewrite]

    # P1-4: hydrate existing directive into Alpine init state
    existing = run.rewrite_directive
    initial_transforms = []
    initial_free_text = ""
    if existing:
        initial_transforms = [
            {"id": t.id, "name_zh": t.name_zh, "params": t.params, "order": t.order}
            for t in sorted(existing.transforms, key=lambda x: x.order)
        ]
        initial_free_text = existing.free_text or ""

    return templates.TemplateResponse(request, "rewrite_directive.html", {
        "run": run,
        "eligible_count": len(eligible),
        "failed_count": sum(1 for p in run.source_prompts if p.failed_to_rewrite),
        "cards_ui": cards_to_ui_dict(),
        "initial_transforms": initial_transforms,
        "initial_free_text": initial_free_text,
        "phase_order": PHASE_ORDER,
        "phase_label": PHASE_LABEL_REWRITE,
    })


@app.post("/rewrite/{run_id}/directive")
async def rewrite_directive_save(run_id: str, request: Request):
    """R2 save: accept JSON RewriteDirective, store on run."""
    from ..core.rewrite_schema import RewriteDirective, Transform
    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")

    body = await request.json()
    try:
        # Body shape: {transforms: [{id, params, order}], free_text, target_capability}
        transforms = []
        for t in body.get("transforms") or []:
            from ..phases.rewrite_cards import card_for
            card = card_for(t.get("id"))
            if card is None:
                continue
            transforms.append(Transform(
                id=t["id"],
                name_zh=card.name_zh,
                params=t.get("params") or {},
                order=int(t.get("order", 0)),
            ))
        directive = RewriteDirective(
            transforms=transforms,
            free_text=(body.get("free_text") or "").strip(),
            target_capability=body.get("target_capability") or None,
            preserve_original=bool(body.get("preserve_original", True)),
            selected_source_ids=body.get("selected_source_ids") or [],
        )
    except Exception as exc:
        code = "DIRECTIVE_EMPTY" if "至少一项非空" in str(exc) else "DIRECTIVE_CONFLICT"
        return JSONResponse(
            {"ok": False, "code": code, "message": str(exc)},
            status_code=400,
        )

    run.rewrite_directive = directive
    run.updated_at = datetime.now()
    return JSONResponse({"ok": True})


@app.post("/rewrite/{run_id}/start")
def rewrite_start(run_id: str, background_tasks: BackgroundTasks):
    """R3: kick off async rewrite. Returns 303 to a 'generating' page."""
    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")
    if not run.rewrite_directive:
        return JSONResponse(
            {"ok": False, "code": "DIRECTIVE_EMPTY", "message": "请先选卡片或写自由意图"},
            status_code=400,
        )
    # P0-5: atomic check-then-set
    with _REWRITE_STATE_LOCK:
        cur = RUN_REWRITE_STATE.get(run_id, {})
        if cur.get("status") in ("running", "qa_running"):
            return JSONResponse(
                {"ok": False, "code": "ALREADY_RUNNING", "message": "改写已在进行"},
                status_code=409,
            )
        # Reserve the slot before doing any other work
        RUN_REWRITE_STATE[run_id] = {
            "status": "running",
            "done": 0,
            "total": 0,                    # filled below
            "started_at": datetime.now().isoformat(),
            "result": None,
        }
        RUN_REWRITE_CANCEL[run_id] = False

    # Build client
    creds = RUN_CREDS.get(run_id)
    if not creds or not creds.get("api_key"):
        # Roll back the reserved slot
        with _REWRITE_STATE_LOCK:
            RUN_REWRITE_STATE.pop(run_id, None)
        return JSONResponse(
            {"ok": False, "code": "NO_API_KEY", "message": "改写需要 API key"},
            status_code=400,
        )
    client = llm_phases.build_client(
        provider=creds["provider"],
        model=creds.get("model_p2") or creds["model"],
        api_key=creds["api_key"],
        base_url=creds.get("base_url") or None,
    )

    eligible = [p for p in run.source_prompts if p.selected and not p.failed_to_rewrite]
    if run.rewrite_directive.selected_source_ids:
        idset = set(run.rewrite_directive.selected_source_ids)
        eligible = [p for p in eligible if p.source_id in idset]
    # Update total now that we know it
    RUN_REWRITE_STATE[run_id]["total"] = len(eligible)

    # Advance phase so /runs/{id} renders the generating template (with rewrite poller)
    # Phase advance + drop raw rows (source_prompts is the canonical data now)
    run.phase = Phase.P3_QA
    run.updated_at = datetime.now()
    RUN_RAW_ROWS.pop(run_id, None)    # P2-4: free raw file data after canonicalization

    background_tasks.add_task(_run_rewrite_background, run_id, client)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


def _run_rewrite_background(run_id: str, client):
    """Background task: actually do the rewrite."""
    from ..phases.rewrite import rewrite_run
    run = RUNS.get(run_id)
    if not run:
        return

    def _progress(done: int, total: int):
        st = RUN_REWRITE_STATE.get(run_id) or {}
        st["done"] = done
        st["total"] = total
        RUN_REWRITE_STATE[run_id] = st

    def _cancelled() -> bool:
        return RUN_REWRITE_CANCEL.get(run_id, False)

    try:
        result = rewrite_run(run, client, progress_cb=_progress, cancel_flag=_cancelled)

        # Run R4 judges when we have at least one entry; phase always advances
        # to P4 on completion (even if all failed) so user can see what
        # happened instead of being stuck on the generating page.
        if not result.cancelled and result.succeeded > 0:
            RUN_REWRITE_STATE[run_id] = {
                "status": "qa_running",
                "done": _progress_done(run_id),
                "total": RUN_REWRITE_STATE.get(run_id, {}).get("total", 0),
                "result": None,
            }
            try:
                _run_r4_quality(run_id, run, client)
            except Exception as q_exc:
                print(f"[R4 failed but continuing] run={run_id}: {q_exc}", flush=True)

        # P0-1 fix: always advance phase on terminal status (completed OR all-failed),
        # not only when succeeded>0. Otherwise UI gets stuck in redirect loop.
        if not result.cancelled:
            run.phase = Phase.P4_REVIEW

        RUN_REWRITE_STATE[run_id] = {
            "status": "cancelled" if result.cancelled else "completed",
            "done": _progress_done(run_id),
            "total": RUN_REWRITE_STATE.get(run_id, {}).get("total", 0),
            "result": {
                "succeeded": result.succeeded,
                "failed": result.failed,
                "cancelled": result.cancelled,
                "elapsed_seconds": round(result.elapsed_seconds, 1),
                "error_breakdown": result.error_breakdown,
            },
        }
        run.updated_at = datetime.now()
    except Exception as exc:
        RUN_REWRITE_STATE[run_id] = {
            "status": "failed",
            "done": _progress_done(run_id),
            "total": RUN_REWRITE_STATE.get(run_id, {}).get("total", 0),
            "result": {"error": f"{type(exc).__name__}: {exc}"},
        }
        print(f"[rewrite-failed] run={run_id}: {exc}", flush=True)


def _progress_done(run_id: str) -> int:
    return RUN_REWRITE_STATE.get(run_id, {}).get("done", 0)


def _run_r4_quality(run_id: str, run, client) -> None:
    """R4: keep score + adherence judges + (optional) existing P3 rules check.

    Mutates run.prompts in place. Stores summary in RUN_QA_REPORTS so the
    review page can show it (reusing the existing QA panel + adding rewrite-
    specific fields).
    """
    import time
    from ..qa.rewrite_quality import (
        measure_keep_scores, measure_adherence_scores, attach_scores_to_entries,
    )
    from ..qa.rules import check_one

    # Build (SourcePrompt, PromptEntry) pairs by source_id
    sp_by_id = {sp.source_id: sp for sp in run.source_prompts}
    pairs: list[tuple] = []
    for pe in run.prompts:
        sid = pe.source_id
        if sid and sid in sp_by_id:
            pairs.append((sp_by_id[sid], pe))

    if not pairs:
        return

    # Rules pass (lightweight, no LLM). For rewrite entries:
    # - sl2_covered / axes_values checks: N/A (rewrite is capability-free)
    # - English-only length checks: skip if prompt_en is empty (zh-only OK)
    REWRITE_IRRELEVANT_PREFIXES = (
        "sl2_covered is empty",
        "axes_values is empty",
    )
    rule_fails = 0
    for _, pe in pairs:
        all_errs = check_one(pe)
        relevant = []
        for e in all_errs:
            if e in REWRITE_IRRELEVANT_PREFIXES:
                continue
            # If en is empty, skip en-length errors (rewrite may be zh-only)
            if not pe.prompt_en and "prompt_en" in e:
                continue
            relevant.append(e)
        pe.qa_rule_errors = relevant
        if relevant:
            rule_fails += 1

    # Keep + adherence judges (LLM)
    t0 = time.time()
    keep = measure_keep_scores(pairs, client)
    print(f"[LLM-timing] keep done in {time.time()-t0:.1f}s  run_id={run_id}", flush=True)

    t0 = time.time()
    adh = measure_adherence_scores(pairs, run.rewrite_directive, client)
    print(f"[LLM-timing] adherence done in {time.time()-t0:.1f}s  run_id={run_id}", flush=True)

    summary = attach_scores_to_entries(pairs, keep, adh)

    # P0-6 fix: don't treat "no score" as "pass". When a judge batch failed,
    # the score is None — we mark needs_human_review and clear qa_passed.
    KEEP_TH, ADH_TH = 5, 7
    for _, pe in pairs:
        rule_ok = not pe.qa_rule_errors
        has_keep = pe.rewrite_kept_score is not None
        has_adh = pe.rewrite_adherence_score is not None
        k_ok = has_keep and pe.rewrite_kept_score >= KEEP_TH
        a_ok = has_adh and pe.rewrite_adherence_score >= ADH_TH

        if not has_keep or not has_adh:
            # Judge didn't return a score for this entry — explicitly UNKNOWN,
            # not "pass". UI shows ⚠ "未打分" so user reviews manually.
            pe.qa_passed = False
            pe.needs_human_review = True
        else:
            pe.qa_passed = bool(rule_ok and k_ok and a_ok)
            pe.needs_human_review = not pe.qa_passed

    RUN_QA_REPORTS[run_id] = {
        "total": summary["total"],
        "passed": summary["both_pass"],
        "pass_rate": (summary["both_pass"] / summary["total"]) if summary["total"] else 0,
        "fail_rules": rule_fails,
        "fail_naturalness": 0,            # not applicable for rewrite (judges focus on intent)
        "fail_coverage": summary["total"] - summary["both_pass"],
        "naturalness_zh_avg": 0,
        "naturalness_en_avg": 0,
        "stress_ratio": sum(1 for _, pe in pairs if pe.is_stress) / summary["total"]
                          if summary["total"] else 0,
        "sl2_uncovered": [],
        "judges_ran": True,
        # Rewrite-specific fields (review.html new branch will read these):
        "keep_avg": summary["keep_avg"],
        "adherence_avg": summary["adherence_avg"],
        "keep_pass": summary["keep_pass"],
        "adherence_pass": summary["adherence_pass"],
    }


@app.post("/rewrite/{run_id}/cancel")
def rewrite_cancel(run_id: str):
    """R3 cancel: signal background task to stop after current batch."""
    if run_id not in RUN_REWRITE_STATE:
        return JSONResponse(
            {"ok": False, "code": "NOT_RUNNING", "message": "没有改写任务可取消"},
            status_code=409,
        )
    RUN_REWRITE_CANCEL[run_id] = True
    return JSONResponse({"ok": True, "message": "已发取消信号,等待当前批次完成"})


@app.post("/rewrite/{run_id}/accept/{prompt_id}")
def rewrite_accept(run_id: str, prompt_id: str, decision: str = Form("accept")):
    """Set rewrite_accepted on a single PromptEntry. Returns JSON for AJAX."""
    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")
    target = next((p for p in run.prompts if p.id == prompt_id), None)
    if target is None:
        return JSONResponse(
            {"ok": False, "code": "PROMPT_NOT_FOUND", "message": f"id={prompt_id} 不存在"},
            status_code=404,
        )
    if decision == "accept":
        target.rewrite_accepted = True
    elif decision == "reject":
        target.rewrite_accepted = False
    elif decision == "unset":
        target.rewrite_accepted = None
    else:
        return JSONResponse(
            {"ok": False, "code": "BAD_DECISION", "message": "decision 必须是 accept/reject/unset"},
            status_code=400,
        )
    run.updated_at = datetime.now()
    return JSONResponse({"ok": True, "id": prompt_id, "decision": decision})


@app.post("/rewrite/{run_id}/iterate")
async def rewrite_iterate(run_id: str, request: Request, background_tasks: BackgroundTasks):
    """R5 iteration: redo the rejected subset with appended refinement."""
    from ..phases.rewrite import iterate_rewrite as iterate_fn

    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")

    body = await request.json()
    rejected = body.get("rejected_ids") or []
    refinement = (body.get("refinement") or "").strip()

    if not rejected:
        return JSONResponse({"ok": False, "code": "NO_REJECTED",
                              "message": "没有被拒绝的条目可改"}, status_code=400)
    if run.rewrite_round >= run.rewrite_max_rounds:
        return JSONResponse({"ok": False, "code": "MAX_ROUNDS_REACHED",
                              "message": f"已用完 {run.rewrite_max_rounds} 轮迭代"},
                             status_code=400)
    # P1-6: only iterate from review phase
    if run.phase != Phase.P4_REVIEW:
        return JSONResponse({"ok": False, "code": "BAD_PHASE",
                              "message": f"只能在审核页迭代,当前 phase={run.phase.value}"},
                             status_code=400)

    # P0-5: atomic check + reserve slot
    with _REWRITE_STATE_LOCK:
        cur = RUN_REWRITE_STATE.get(run_id, {})
        if cur.get("status") in ("running", "qa_running"):
            return JSONResponse({"ok": False, "code": "ALREADY_RUNNING",
                                  "message": "另一个改写任务在进行"},
                                 status_code=409)
        RUN_REWRITE_STATE[run_id] = {
            "status": "running",
            "done": 0,
            "total": len(rejected),
            "started_at": datetime.now().isoformat(),
            "result": None,
        }
        RUN_REWRITE_CANCEL[run_id] = False

    creds = RUN_CREDS.get(run_id)
    if not creds or not creds.get("api_key"):
        with _REWRITE_STATE_LOCK:
            RUN_REWRITE_STATE.pop(run_id, None)
        return JSONResponse({"ok": False, "code": "NO_API_KEY",
                              "message": "改写需要 API key"}, status_code=400)
    client = llm_phases.build_client(
        provider=creds["provider"],
        model=creds.get("model_p2") or creds["model"],
        api_key=creds["api_key"],
        base_url=creds.get("base_url") or None,
    )

    run.phase = Phase.P3_QA
    run.updated_at = datetime.now()

    background_tasks.add_task(_run_iterate_background, run_id, rejected, refinement, client)
    return JSONResponse({"ok": True, "round": run.rewrite_round + 1})


def _run_iterate_background(run_id: str, rejected_ids: list[str], refinement: str, client):
    """Background: iterate_rewrite + re-run R4 on the affected subset."""
    from ..phases.rewrite import iterate_rewrite as iterate_fn
    run = RUNS.get(run_id)
    if not run:
        return

    # P1-2: progress callback for iterate path
    def _progress(done: int, total: int):
        st = RUN_REWRITE_STATE.get(run_id) or {}
        st["done"] = done
        st["total"] = total
        RUN_REWRITE_STATE[run_id] = st

    try:
        result = iterate_fn(run, rejected_ids, refinement, client, progress_cb=_progress)
        # Re-run R4 quality scores on the whole set (cheap enough)
        try:
            _run_r4_quality(run_id, run, client)
        except Exception:
            pass
        RUN_REWRITE_STATE[run_id] = {
            "status": "completed",
            "done": len(rejected_ids),
            "total": len(rejected_ids),
            "result": {
                "succeeded": result.succeeded,
                "failed": result.failed,
                "elapsed_seconds": round(result.elapsed_seconds, 1),
                "round": run.rewrite_round,
            },
        }
        run.phase = Phase.P4_REVIEW
        run.updated_at = datetime.now()
    except Exception as exc:
        RUN_REWRITE_STATE[run_id] = {
            "status": "failed",
            "done": 0, "total": len(rejected_ids),
            "result": {"error": f"{type(exc).__name__}: {exc}"},
        }
        # P0-1 fix (iterate path): also advance phase so UI doesn't loop
        run.phase = Phase.P4_REVIEW
        run.updated_at = datetime.now()
        print(f"[iterate-failed] run={run_id}: {exc}", flush=True)


@app.post("/rewrite/{run_id}/confirm")
def rewrite_confirm(run_id: str):
    """R5 confirm: advance to R6 (export). Unreviewed entries default to accepted."""
    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Not a rewrite run")

    total = len(run.prompts)
    if total == 0:
        return JSONResponse({"ok": False, "code": "EMPTY",
                              "message": "没有改写产物可确认"}, status_code=400)
    reviewed = sum(1 for p in run.prompts if p.rewrite_accepted is not None)
    review_rate = reviewed / total if total else 0
    if review_rate < 0.8:
        return JSONResponse({
            "ok": False, "code": "INSUFFICIENT_REVIEW",
            "message": f"已审核 {reviewed}/{total} (<80%),先全部走完",
        }, status_code=400)

    # Default unreviewed → accepted
    for p in run.prompts:
        if p.rewrite_accepted is None:
            p.rewrite_accepted = True

    # Drop rejected entries from the final set
    run.prompts = [p for p in run.prompts if p.rewrite_accepted]

    run.phase = Phase.P5_EXPORT
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/rewrite/{run_id}/progress")
def rewrite_progress(run_id: str):
    """Polled by the UI 'generating' page."""
    st = RUN_REWRITE_STATE.get(run_id)
    if not st:
        return JSONResponse({"status": "not_started", "done": 0, "total": 0})
    return JSONResponse(st)


@app.post("/runs/{run_id}/slug")
async def update_slug(run_id: str, slug: str = Form(...)):
    """Manually override the capability slug (snake_case ASCII required)."""
    run = _get_run(run_id)
    slug = slug.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", slug):
        raise HTTPException(400, f"Slug must be snake_case ASCII, got: {slug!r}")
    run.capability_slug = slug
    if run_id in RUN_INTAKE:
        RUN_INTAKE[run_id] = {
            **RUN_INTAKE[run_id],
            "slug": slug,
            "confidence": "user_set",
            "source": "user",
        }
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


# ---------------------------------------------------------------------------
# Phase 1 — dimensions
# ---------------------------------------------------------------------------

@app.post("/runs/{run_id}/p1/regenerate")
def p1_regenerate(run_id: str, background_tasks: BackgroundTasks, free_text: str = Form("")):
    run = _get_run(run_id)
    if run.phase != Phase.P1_DIMENSIONS:
        raise HTTPException(400, "Not in P1")

    if run.p1_round >= run.p1_max_rounds:
        # Force confirm
        return p1_confirm(run_id)

    run.p1_round += 1
    try:
        run.sl2_list, run.axes, new_rec = _try_real_dimensions(run_id, run, feedback=free_text)
        if not new_rec:
            new_rec = mock_data.default_recommended_tags(run.capability_slug)
        run.recommended_tags = new_rec
        run.original_ai_tags = {k: list(v) for k, v in new_rec.items()}
    except Exception:
        run.sl2_list, run.axes = mock_data.generate_mock_dimensions(
            run.user_description, round=run.p1_round, feedback=free_text,
            capability_slug=run.capability_slug
        )
        run.recommended_tags = mock_data.default_recommended_tags(run.capability_slug)
        run.original_ai_tags = {k: list(v) for k, v in run.recommended_tags.items()}
    # Recompute target_set_size based on updated axes
    from ..phases.dimensions import compute_min_set_size
    run.target_set_size = compute_min_set_size(run.axes)
    # Re-judge in background
    RUN_DIM_CRITIQUE[run_id] = {"pending": True}
    background_tasks.add_task(_run_dim_judge_background, run_id)
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/p1/confirm")
def p1_confirm(run_id: str):
    run = _get_run(run_id)

    # ---- P2: generate prompts ----
    run.phase = Phase.P2_PROMPTS
    try:
        run.prompts = _try_real_prompts(run_id, run)
        if not run.prompts:
            raise RuntimeError("empty LLM response")
    except Exception:
        run.prompts = mock_data.generate_mock_prompts(
            run.sl2_list, run.axes, run.target_set_size or 60
        )

    # ---- P3: QA gate (rules + LLM naturalness + coverage audit) ----
    run.phase = Phase.P3_QA
    try:
        report = _try_real_qa(run_id, run)
        RUN_QA_REPORTS[run_id] = {
            "total": report.total,
            "passed": report.passed,
            "pass_rate": report.pass_rate,
            "fail_rules": report.fail_rules,
            "fail_naturalness": report.fail_naturalness,
            "fail_coverage": report.fail_coverage,
            "naturalness_zh_avg": round(report.naturalness_zh_avg, 1),
            "naturalness_en_avg": round(report.naturalness_en_avg, 1),
            "stress_ratio": round(report.stress_ratio, 2),
            "sl2_uncovered": report.sl2_uncovered,
            "judges_ran": report.judges_ran,
        }
    except Exception as e:
        # QA failed entirely — log but don't block the user
        RUN_QA_REPORTS[run_id] = {"error": str(e), "judges_ran": False}

    # ---- P4: hand off to human review ----
    run.phase = Phase.P4_REVIEW
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/p4/rerun_qa")
def p4_rerun_qa(run_id: str):
    """Re-run the QA pass on the current prompts. Useful after user edits."""
    run = _get_run(run_id)
    try:
        report = _try_real_qa(run_id, run)
        RUN_QA_REPORTS[run_id] = {
            "total": report.total,
            "passed": report.passed,
            "pass_rate": report.pass_rate,
            "fail_rules": report.fail_rules,
            "fail_naturalness": report.fail_naturalness,
            "fail_coverage": report.fail_coverage,
            "naturalness_zh_avg": round(report.naturalness_zh_avg, 1),
            "naturalness_en_avg": round(report.naturalness_en_avg, 1),
            "stress_ratio": round(report.stress_ratio, 2),
            "sl2_uncovered": report.sl2_uncovered,
            "judges_ran": report.judges_ran,
        }
    except Exception as e:
        RUN_QA_REPORTS[run_id] = {"error": str(e), "judges_ran": False}
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


# ---------------------------------------------------------------------------
# Phase 4 — review
# ---------------------------------------------------------------------------

@app.post("/runs/{run_id}/p4/edit/{prompt_id}")
async def p4_edit_prompt(run_id: str, prompt_id: str,
                          prompt_zh: str = Form(...),
                          prompt_en: str = Form(...)):
    run = _get_run(run_id)
    for p in run.prompts:
        if p.id == prompt_id:
            p.prompt_zh = prompt_zh
            p.prompt_en = prompt_en
            p.generation_round += 1
            break
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/p4/drop/{prompt_id}")
async def p4_drop_prompt(run_id: str, prompt_id: str):
    run = _get_run(run_id)
    run.prompts = [p for p in run.prompts if p.id != prompt_id]
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/p4/regenerate")
def p4_regenerate(run_id: str, free_text: str = Form("")):
    run = _get_run(run_id)
    if run.p4_round >= run.p4_max_rounds:
        return p4_confirm(run_id)
    run.p4_round += 1
    # Mock regen: shuffle some prompts
    new_prompts = mock_data.generate_mock_prompts(
        run.sl2_list, run.axes, run.target_set_size or 60
    )
    # Keep ~half of existing + replace half
    keep = run.prompts[:len(run.prompts)//2]
    run.prompts = keep + new_prompts[:len(new_prompts) - len(keep)]
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/p4/confirm")
def p4_confirm(run_id: str):
    run = _get_run(run_id)
    run.phase = Phase.P5_EXPORT
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


# ---------------------------------------------------------------------------
# Phase 5 — export / downloads
# ---------------------------------------------------------------------------

def _content_disposition(filename: str) -> str:
    """Build a Content-Disposition header that handles non-ASCII filenames
    via RFC 5987 (filename*= utf-8 encoding) plus an ASCII fallback.

    Note: \\w in Python is Unicode-aware, so we use explicit ASCII class.
    """
    from urllib.parse import quote
    # ASCII-only fallback filename (strict — strip dangerous header chars too)
    ascii_name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", filename) or "download"
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


@app.get("/runs/{run_id}/download/prompts.jsonl")
async def download_prompts(run_id: str):
    run = _get_run(run_id)
    lines = [p.model_dump_json() for p in run.prompts]
    body = "\n".join(lines)
    name = f"prompts_{run.capability_slug}_v{run.inherited_from_version or 1}.jsonl"
    return Response(body, media_type="application/x-jsonlines",
                    headers={"Content-Disposition": _content_disposition(name)})


@app.get("/runs/{run_id}/download/handbook.md")
async def download_handbook_md(run_id: str):
    run = _get_run(run_id)
    md = f"# {run.capability_slug} ｜ 评测维度说明书 v1\n\n"
    md += f"## 概述\n用户描述: {run.user_description}\n\n"
    md += "## SL2 列表(评测员勾选项)\n\n"
    for i, sl2 in enumerate(run.sl2_list, 1):
        md += f"### {i}. {sl2.name}（`{sl2.id}`）\n\n"
        md += f"**描述**: {sl2.description}\n\n"
        md += sl2.judging_criteria_md + "\n\n"
        md += f"**Stress 关键词**: {', '.join(sl2.stress_keywords)}\n\n"
        md += "**示例帧**: [Pass 示例] | [Fail 示例]（v1 占位,v2 接入真实生成结果）\n\n"
        md += "---\n\n"
    md += "## 评测员流程\n1. 看视频 A + 视频 B(同一 prompt)\n2. 对每个 SL2 维度勾选 A/B 是否触发\n3. GSB 总判定:A 比 B 好 / 相同 / 差\n"
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition":
                             _content_disposition(f"handbook_{run.capability_slug}.md")})


@app.get("/runs/{run_id}/download/handbook.json")
async def download_handbook_json(run_id: str):
    run = _get_run(run_id)
    data = {
        "capability": run.capability_slug,
        "capability_version": 1,
        "schema_version": 1,
        "sl2_items": [
            {
                "id": s.id,
                "name_zh": s.name,
                "description_zh": s.description,
                "yes_criteria_zh": s.judging_criteria_md,
                "stress_keywords": s.stress_keywords,
                "weight": 1.0,
            }
            for s in run.sl2_list
        ],
    }
    return JSONResponse(data, headers={
        "Content-Disposition": _content_disposition(f"handbook_{run.capability_slug}.json")
    })


@app.get("/runs/{run_id}/download/rewrite_diff.jsonl")
async def download_rewrite_diff(run_id: str):
    """Per-prompt diff report. Only meaningful for source='rewrite' runs.

    One line per PromptEntry — original (from SourcePrompt) joined with
    rewritten + diff text + keep/adherence scores + accept decision.
    """
    run = _get_run(run_id)
    if run.source != "rewrite":
        raise HTTPException(400, "Diff report only available for rewrite runs")
    if not run.prompts:
        # Return empty file rather than 500
        return Response(
            "", media_type="application/x-jsonlines",
            headers={"Content-Disposition":
                     _content_disposition(f"rewrite_diff_{run.id}_empty.jsonl")},
        )

    # Build {source_id: SourcePrompt} for quick lookup
    sp_by_id = {sp.source_id: sp for sp in run.source_prompts}

    lines: list[str] = []
    for p in run.prompts:
        sp = sp_by_id.get(p.source_id) if p.source_id else None
        record = {
            "id": p.id,
            "source_id": p.source_id,
            "original_text": sp.original_text if sp else None,
            "original_text_en": sp.original_text_en if sp else None,
            "original_metadata": sp.metadata if sp else None,
            "prompt_zh": p.prompt_zh,
            "prompt_en": p.prompt_en or None,
            "rewrite_diff": p.rewrite_diff,
            "rewrite_kept_score": p.rewrite_kept_score,
            "rewrite_adherence_score": p.rewrite_adherence_score,
            "rewrite_accepted": p.rewrite_accepted,
            "qa_passed": p.qa_passed,
            "qa_rule_errors": p.qa_rule_errors,
            "subject_type": p.subject_type,
            "subject_count": p.subject_count,
            "difficulty": p.difficulty,
            "is_stress": p.is_stress,
        }
        # Directive snapshot (same across all entries — included once via header
        # would be lighter, but per-line keeps the file self-contained)
        if run.rewrite_directive:
            record["directive"] = {
                "transforms": [
                    {"id": t.id, "name_zh": t.name_zh, "params": t.params, "order": t.order}
                    for t in run.rewrite_directive.transforms
                ],
                "free_text": run.rewrite_directive.free_text,
                "target_capability": run.rewrite_directive.target_capability,
            }
        lines.append(json.dumps(record, ensure_ascii=False, default=str))

    body = "\n".join(lines)
    return Response(
        body,
        media_type="application/x-jsonlines",
        headers={"Content-Disposition":
                 _content_disposition(f"rewrite_diff_{run.id}.jsonl")},
    )


@app.get("/runs/{run_id}/download/coverage.json")
async def download_coverage(run_id: str):
    run = _get_run(run_id)
    return JSONResponse(
        mock_data.compute_coverage_matrix(run.prompts, run.sl2_list, run.axes),
        headers={"Content-Disposition":
                 _content_disposition(f"coverage_{run.capability_slug}.json")},
    )


# ---------------------------------------------------------------------------
# Run management
# ---------------------------------------------------------------------------

def _cleanup_run_state(run_id: str) -> None:
    """P0-4: clean every state dict that may reference run_id.

    Centralized so adding new state dicts doesn't risk leaks.
    """
    for d in (RUNS, RUN_CREDS, RUN_RAW_ROWS, RUN_REWRITE_STATE,
              RUN_REWRITE_CANCEL, RUN_QA_REPORTS, RUN_DIM_CRITIQUE,
              RUN_LAST_ERROR, RUN_INTAKE):
        d.pop(run_id, None)


@app.post("/runs/{run_id}/delete")
async def delete_run(run_id: str):
    _cleanup_run_state(run_id)
    return RedirectResponse("/", status_code=303)


@app.get("/api/runs/{run_id}/state")
async def api_run_state(run_id: str):
    run = _get_run(run_id)
    return JSONResponse(run.model_dump(mode="json"))
