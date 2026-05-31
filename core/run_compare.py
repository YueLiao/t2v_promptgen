"""Run vs Run diff — used by /compare route and CLI `diff`.

Compares two Runs along four axes:

  1. Headline numbers (SL2 count, axes count, prompt count, pass rates,
     scores, ...) — for the side-by-side stats table.

  2. SL2 set diff (common / only-A / only-B) by id.

  3. 8-dim recommended_tags diff (per dimension).

  4. Prompt pool diff:
     - Exact matches (same prompt_zh)
     - Near matches (small edit distance ratio)
     - A-only / B-only

Pure stdlib, no heavy deps. Edit-distance similarity via
difflib.SequenceMatcher.ratio() — fast and good enough for ~100-prompt
pools to flag near-duplicates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

from .schema import Axis, PromptEntry, Run, SL2


# ---------------------------------------------------------------------------
# Headline numbers
# ---------------------------------------------------------------------------

@dataclass
class Headline:
    """Side-by-side scalar metrics."""
    label: str
    a: object
    b: object
    delta: str = ""           # human-readable Δ (e.g. "+2", "-13%", "")
    flag: str = ""            # "warn" / "good" / "" for color hints


def _pct(num: float | int, denom: float | int) -> float:
    return (num / denom) if denom else 0.0


def _avg_score(prompts: Iterable[PromptEntry], field_name: str) -> float | None:
    vals = [getattr(p, field_name) for p in prompts
            if getattr(p, field_name, None) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _pass_rate(prompts: list[PromptEntry]) -> float | None:
    if not prompts:
        return None
    return round(sum(1 for p in prompts if p.qa_passed) / len(prompts), 3)


def compute_headlines(a: Run, b: Run) -> list[Headline]:
    """Build the side-by-side stats table."""
    out: list[Headline] = []

    def add(label, av, bv, delta="", flag=""):
        out.append(Headline(label, av, bv, delta, flag))

    add("能力", a.capability_slug, b.capability_slug,
        "同" if a.capability_slug == b.capability_slug else "≠")
    add("任务类型", a.source, b.source,
        "同" if a.source == b.source else "≠")
    add("phase", a.phase.value, b.phase.value)

    # Designs
    na, nb = len(a.sl2_list), len(b.sl2_list)
    add("SL2 数", na, nb, _delta_str(na, nb))
    na, nb = len(a.axes), len(b.axes)
    add("axes 数", na, nb, _delta_str(na, nb))

    ta = sum(len(v) for v in a.recommended_tags.values())
    tb = sum(len(v) for v in b.recommended_tags.values())
    add("推荐 tag 总数", ta, tb, _delta_str(ta, tb))

    # Outputs
    add("目标 prompt 数", a.target_set_size or "-", b.target_set_size or "-",
        _delta_str(a.target_set_size or 0, b.target_set_size or 0))
    add("实际 prompt 数", len(a.prompts), len(b.prompts),
        _delta_str(len(a.prompts), len(b.prompts)))

    # QA quality
    pra = _pass_rate(a.prompts)
    prb = _pass_rate(b.prompts)
    if pra is not None or prb is not None:
        delta = ""
        flag = ""
        if pra is not None and prb is not None:
            d = (prb - pra) * 100
            delta = f"{d:+.0f}%"
            if d <= -10:
                flag = "warn"
            elif d >= 5:
                flag = "good"
        add("P3 通过率",
            f"{pra:.0%}" if pra is not None else "-",
            f"{prb:.0%}" if prb is not None else "-",
            delta, flag)

    nz_a = _avg_score(a.prompts, "qa_naturalness_zh")
    nz_b = _avg_score(b.prompts, "qa_naturalness_zh")
    if nz_a is not None or nz_b is not None:
        delta = f"{(nz_b or 0) - (nz_a or 0):+.1f}" if (nz_a and nz_b) else ""
        add("自然度(中)均", nz_a or "-", nz_b or "-", delta)

    # Rewrite-specific
    if a.source == "rewrite" or b.source == "rewrite":
        ka = _avg_score(a.prompts, "rewrite_kept_score")
        kb = _avg_score(b.prompts, "rewrite_kept_score")
        if ka is not None or kb is not None:
            add("保持率均", ka or "-", kb or "-")
        aa = _avg_score(a.prompts, "rewrite_adherence_score")
        ab = _avg_score(b.prompts, "rewrite_adherence_score")
        if aa is not None or ab is not None:
            add("遵循度均", aa or "-", ab or "-")

    add("provider/model", a.model, b.model)
    return out


def _delta_str(a: int, b: int) -> str:
    if a == b:
        return ""
    return f"{b - a:+d}"


# ---------------------------------------------------------------------------
# SL2 + axes + tag diffs
# ---------------------------------------------------------------------------

@dataclass
class SetDiff:
    common: list[str] = field(default_factory=list)
    only_a: list[str] = field(default_factory=list)
    only_b: list[str] = field(default_factory=list)


def diff_sl2(a: Run, b: Run) -> SetDiff:
    """By SL2.id."""
    aids = {s.id: s for s in a.sl2_list}
    bids = {s.id: s for s in b.sl2_list}
    return SetDiff(
        common=sorted(aids.keys() & bids.keys()),
        only_a=sorted(aids.keys() - bids.keys()),
        only_b=sorted(bids.keys() - aids.keys()),
    )


def diff_axes(a: Run, b: Run) -> SetDiff:
    """By axis.name (ignoring values for top-level diff)."""
    aset = {ax.name for ax in a.axes}
    bset = {ax.name for ax in b.axes}
    return SetDiff(
        common=sorted(aset & bset),
        only_a=sorted(aset - bset),
        only_b=sorted(bset - aset),
    )


@dataclass
class TagDimDiff:
    """Per-D# diff of recommended_tags."""
    dim: str           # "D1".."D8"
    common: list[str]
    only_a: list[str]
    only_b: list[str]


def diff_recommended_tags(a: Run, b: Run) -> list[TagDimDiff]:
    """Per-dimension tag diff. Includes dimensions where either has any tag."""
    all_dims = sorted(set(a.recommended_tags.keys()) | set(b.recommended_tags.keys()))
    out = []
    for d in all_dims:
        av = set(a.recommended_tags.get(d, []))
        bv = set(b.recommended_tags.get(d, []))
        out.append(TagDimDiff(
            dim=d,
            common=sorted(av & bv),
            only_a=sorted(av - bv),
            only_b=sorted(bv - av),
        ))
    return out


# ---------------------------------------------------------------------------
# Prompt pool diff
# ---------------------------------------------------------------------------

@dataclass
class PromptMatch:
    a: PromptEntry | None
    b: PromptEntry | None
    similarity: float       # 0..1; 1.0 = exact match


@dataclass
class PromptPoolDiff:
    """Aggregated pool diff."""
    exact: list[PromptMatch]         # similarity == 1.0
    near: list[PromptMatch]          # 0.7 <= similarity < 1.0
    only_a: list[PromptEntry]        # didn't match anything in B above 0.7
    only_b: list[PromptEntry]        # ditto, mirror

    @property
    def total_a(self) -> int: return len(self.exact) + len(self.near) + len(self.only_a)
    @property
    def total_b(self) -> int: return len(self.exact) + len(self.near) + len(self.only_b)


_NEAR_THRESHOLD = 0.70    # SequenceMatcher.ratio() cutoff for "similar"


def _similarity(s1: str, s2: str) -> float:
    """Edit-distance-based similarity 0..1 (1=identical)."""
    if not s1 or not s2:
        return 0.0
    # Quick exact check first
    if s1 == s2:
        return 1.0
    return SequenceMatcher(None, s1, s2, autojunk=False).ratio()


def diff_prompts(a: Run, b: Run, near_threshold: float = _NEAR_THRESHOLD) -> PromptPoolDiff:
    """Greedy best-match pairing.

    For each B prompt, find the best A prompt by similarity. If ≥1.0
    → exact bin; if ≥ threshold → near bin; otherwise B-only.
    Remaining unmatched A prompts → A-only.
    """
    a_prompts = list(a.prompts)
    b_prompts = list(b.prompts)

    # Index A by prompt_zh for O(1) exact-match
    a_by_zh: dict[str, list[PromptEntry]] = {}
    for p in a_prompts:
        a_by_zh.setdefault(p.prompt_zh, []).append(p)

    exact: list[PromptMatch] = []
    near: list[PromptMatch] = []
    only_b: list[PromptEntry] = []
    matched_a: set[int] = set()    # id() of consumed A entries

    for pb in b_prompts:
        # Try exact match first
        ea = a_by_zh.get(pb.prompt_zh)
        if ea:
            chosen = next((p for p in ea if id(p) not in matched_a), None)
            if chosen:
                matched_a.add(id(chosen))
                exact.append(PromptMatch(a=chosen, b=pb, similarity=1.0))
                continue

        # Near-match search across unmatched A prompts
        # For pools ≤ ~500 this is O(N²) but fast in practice; use early exit
        best = None
        best_sim = 0.0
        for pa in a_prompts:
            if id(pa) in matched_a:
                continue
            sim = _similarity(pa.prompt_zh, pb.prompt_zh)
            if sim > best_sim:
                best_sim, best = sim, pa
                if sim >= 0.95:    # close enough; stop early
                    break
        if best and best_sim >= near_threshold:
            matched_a.add(id(best))
            near.append(PromptMatch(a=best, b=pb, similarity=round(best_sim, 3)))
        else:
            only_b.append(pb)

    only_a = [p for p in a_prompts if id(p) not in matched_a]
    return PromptPoolDiff(exact=exact, near=near, only_a=only_a, only_b=only_b)


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------

@dataclass
class CompareReport:
    a: Run
    b: Run
    headlines: list[Headline]
    sl2_diff: SetDiff
    axes_diff: SetDiff
    tag_diff: list[TagDimDiff]
    prompt_diff: PromptPoolDiff


def compare_runs(a: Run, b: Run) -> CompareReport:
    """Full diff between two runs."""
    return CompareReport(
        a=a, b=b,
        headlines=compute_headlines(a, b),
        sl2_diff=diff_sl2(a, b),
        axes_diff=diff_axes(a, b),
        tag_diff=diff_recommended_tags(a, b),
        prompt_diff=diff_prompts(a, b),
    )
