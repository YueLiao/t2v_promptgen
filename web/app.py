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
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
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


def _ctx(run: Run, **extra) -> dict:
    """Build common template context."""
    return {
        "run": run,
        "phase_order": PHASE_ORDER,
        "phase_label": PHASE_LABEL_ZH,
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
    })


# ---------------------------------------------------------------------------
# Create + view run
# ---------------------------------------------------------------------------

@app.post("/runs")
async def create_run(
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
    slug = mock_data.mock_slug_for(description)

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
        run.sl2_list, run.axes = _try_real_dimensions(run_id, run)
    except Exception as exc:
        import traceback
        err = f"[P1 LLM 调用失败 → 走 mock] slug={slug}  {type(exc).__name__}: {exc}"
        print(err, flush=True)
        traceback.print_exc()
        RUN_LAST_ERROR[run_id] = err
        run.sl2_list, run.axes = mock_data.generate_mock_dimensions(
            description, round=0, capability_slug=slug
        )

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

    if RUN_LAST_ERROR.get(run_id):
        extra["last_error"] = RUN_LAST_ERROR[run_id]

    return templates.TemplateResponse(request, template, _ctx(run, **extra))


# ---------------------------------------------------------------------------
# Phase 1 — dimensions
# ---------------------------------------------------------------------------

@app.post("/runs/{run_id}/p1/regenerate")
async def p1_regenerate(run_id: str, free_text: str = Form("")):
    run = _get_run(run_id)
    if run.phase != Phase.P1_DIMENSIONS:
        raise HTTPException(400, "Not in P1")

    if run.p1_round >= run.p1_max_rounds:
        # Force confirm
        return await p1_confirm(run_id)

    run.p1_round += 1
    try:
        run.sl2_list, run.axes = _try_real_dimensions(run_id, run, feedback=free_text)
    except Exception:
        run.sl2_list, run.axes = mock_data.generate_mock_dimensions(
            run.user_description, round=run.p1_round, feedback=free_text,
            capability_slug=run.capability_slug
        )
    # Recompute target_set_size based on updated axes
    from ..phases.dimensions import compute_min_set_size
    run.target_set_size = compute_min_set_size(run.axes)
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/p1/confirm")
async def p1_confirm(run_id: str):
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
async def p4_rerun_qa(run_id: str):
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
async def p4_regenerate(run_id: str, free_text: str = Form("")):
    run = _get_run(run_id)
    if run.p4_round >= run.p4_max_rounds:
        return await p4_confirm(run_id)
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
async def p4_confirm(run_id: str):
    run = _get_run(run_id)
    run.phase = Phase.P5_EXPORT
    run.updated_at = datetime.now()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


# ---------------------------------------------------------------------------
# Phase 5 — export / downloads
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}/download/prompts.jsonl")
async def download_prompts(run_id: str):
    run = _get_run(run_id)
    lines = []
    for p in run.prompts:
        lines.append(p.model_dump_json())
    body = "\n".join(lines)
    return Response(body, media_type="application/x-jsonlines",
                    headers={"Content-Disposition": f"attachment; filename=prompts_{run.capability_slug}_v{run.inherited_from_version or 1}.jsonl"})


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
                    headers={"Content-Disposition": f"attachment; filename=handbook_{run.capability_slug}.md"})


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
        "Content-Disposition": f"attachment; filename=handbook_{run.capability_slug}.json"
    })


@app.get("/runs/{run_id}/download/coverage.json")
async def download_coverage(run_id: str):
    run = _get_run(run_id)
    return JSONResponse(mock_data.compute_coverage_matrix(run.prompts, run.sl2_list, run.axes))


# ---------------------------------------------------------------------------
# Run management
# ---------------------------------------------------------------------------

@app.post("/runs/{run_id}/delete")
async def delete_run(run_id: str):
    RUNS.pop(run_id, None)
    return RedirectResponse("/", status_code=303)


@app.get("/api/runs/{run_id}/state")
async def api_run_state(run_id: str):
    run = _get_run(run_id)
    return JSONResponse(run.model_dump(mode="json"))
