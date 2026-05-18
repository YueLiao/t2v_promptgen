"""Phase 3 — Machine QA gate.

Three-tier (all run unconditionally; results stored on each PromptEntry):

  1. Deterministic rules (qa/rules.py)
       length bounds, banned terms, required fields

  2. LLM naturalness (qa/judge.py — batched)
       0-10 ZH and EN score, threshold 7

  3. LLM coverage audit (qa/judge.py — batched)
       independent classification of which SL2 the prompt actually tests,
       compared against generator-declared sl2_covered

Each prompt gets `qa_passed: bool` + `needs_human_review: bool` populated.
Set-level metrics returned in the QAReport:
  - pass rate
  - SL2 cells uncovered (coverage matrix holes)
  - stress ratio
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..core.schema import PromptEntry, SL2, Axis
from ..llm.base import LLMClient
from ..qa.rules import check_one
from ..qa.judge import naturalness_batch, coverage_audit_batch

NATURALNESS_THRESHOLD = 7


@dataclass
class QAReport:
    """Set-level QA summary returned by run()."""
    total: int = 0
    passed: int = 0
    fail_rules: int = 0
    fail_naturalness: int = 0
    fail_coverage: int = 0
    naturalness_zh_avg: float = 0.0
    naturalness_en_avg: float = 0.0
    stress_ratio: float = 0.0
    sl2_uncovered: list[str] = field(default_factory=list)
    judges_ran: bool = False    # False when no LLM client was supplied

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def run(
    prompts: list[PromptEntry],
    sl2_list: list[SL2],
    axes: list[Axis],
    client: LLMClient | None = None,
) -> QAReport:
    """Run all QA tiers on `prompts`, mutate in place, return set-level report.

    If `client` is None, only the deterministic rules run; LLM tiers are skipped
    and prompts default to qa_passed=True iff rules pass.
    """
    report = QAReport(total=len(prompts))
    if not prompts:
        return report

    # ---- Tier 1: rules ----
    for p in prompts:
        p.qa_rule_errors = check_one(p)

    # ---- Tier 2 + 3: LLM judges (only if client provided) ----
    nat_scores: dict[str, dict] = {}
    judged_sl2: dict[str, list[str]] = {}
    if client is not None:
        try:
            nat_scores = naturalness_batch(prompts, client)
        except Exception:
            nat_scores = {}
        try:
            judged_sl2 = coverage_audit_batch(prompts, sl2_list, client)
        except Exception:
            judged_sl2 = {}
        report.judges_ran = bool(nat_scores or judged_sl2)

    # ---- Apply judge results to each prompt + compute aggregate flags ----
    zh_scores: list[int] = []
    en_scores: list[int] = []

    for p in prompts:
        # Naturalness
        ns = nat_scores.get(p.id)
        if ns:
            p.qa_naturalness_zh = ns["zh"]
            p.qa_naturalness_en = ns["en"]
            p.qa_naturalness_issues = ns.get("issues", [])
            zh_scores.append(ns["zh"])
            en_scores.append(ns["en"])

        # Coverage audit
        if p.id in judged_sl2:
            p.qa_judged_sl2 = judged_sl2[p.id]
            declared = set(p.sl2_covered)
            actual = set(p.qa_judged_sl2)
            # Match if at least 1 declared SL2 is also in judge's list
            p.qa_coverage_match = bool(declared & actual) if declared else False

        # Aggregate pass flag
        rule_pass = not p.qa_rule_errors
        nat_pass = (
            p.qa_naturalness_zh is None  # judge didn't run = don't block
            or (p.qa_naturalness_zh >= NATURALNESS_THRESHOLD
                and (p.qa_naturalness_en or 0) >= NATURALNESS_THRESHOLD)
        )
        cov_pass = p.qa_coverage_match is None or p.qa_coverage_match

        p.qa_passed = rule_pass and nat_pass and cov_pass
        p.needs_human_review = not p.qa_passed

        # Tally
        if p.qa_passed:
            report.passed += 1
        if not rule_pass:
            report.fail_rules += 1
        if not nat_pass:
            report.fail_naturalness += 1
        if not cov_pass:
            report.fail_coverage += 1

    if zh_scores:
        report.naturalness_zh_avg = sum(zh_scores) / len(zh_scores)
    if en_scores:
        report.naturalness_en_avg = sum(en_scores) / len(en_scores)

    # Stress ratio
    stress_n = sum(1 for p in prompts if p.is_stress)
    report.stress_ratio = stress_n / len(prompts)

    # SL2 coverage matrix — any SL2 never hit by any passing prompt?
    hit_sl2: set[str] = set()
    for p in prompts:
        if p.qa_passed:
            hit_sl2.update(p.sl2_covered)
    report.sl2_uncovered = [s.id for s in sl2_list if s.id not in hit_sl2]

    return report
