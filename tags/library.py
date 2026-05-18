"""Scene library — load the 4-level tag taxonomy and sample concrete scenes.

Source: tag_system.yaml (bundled in this package).
Each Tag instance carries L1>L2>L3>L4 path + raw count (popularity in source data).

Sampling strategies:
    - sample_diverse(k): one tag per L2 category, round-robin until k reached
    - sample_weighted(k): by L4 count (popular scenes more likely)
    - sample_filtered(k, l1, l2): restrict to a specific L1/L2 subtree

Use sample_diverse() as the default for prompt generation — gives broad scene
coverage without bias toward "buildings" or "popular objects".
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import yaml

DEFAULT_TAG_FILE = Path(__file__).parent / "tag_system.yaml"


@dataclass
class Tag:
    l1: str
    l2: str
    l3: str
    l4: str | None = None
    count: int = 0

    def path(self) -> str:
        parts = [self.l1, self.l2, self.l3]
        if self.l4:
            parts.append(self.l4)
        return " > ".join(parts)

    def scene_phrase(self) -> str:
        """Return the most concrete name suitable for embedding in a prompt."""
        return self.l4 or self.l3


class SceneLibrary:
    def __init__(self, tag_file: Path | None = None) -> None:
        self.path = tag_file or DEFAULT_TAG_FILE
        self._tags: list[Tag] = self._load(self.path)

    def _load(self, path: Path) -> list[Tag]:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        out: list[Tag] = []
        for l1_node in doc.get("tag_system", []):
            l1 = l1_node["name"]
            for l2_node in l1_node.get("l2", []):
                l2 = l2_node["name"]
                for l3_node in l2_node.get("l3", []):
                    l3 = l3_node["name"]
                    l4_list = l3_node.get("l4") or []
                    if not l4_list:
                        # Some L3 nodes have no L4 children
                        out.append(Tag(l1=l1, l2=l2, l3=l3))
                        continue
                    for l4_node in l4_list:
                        out.append(Tag(
                            l1=l1, l2=l2, l3=l3,
                            l4=l4_node.get("name"),
                            count=int(l4_node.get("count", 0)),
                        ))
        return out

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def all(self) -> list[Tag]:
        return list(self._tags)

    def l1_categories(self) -> list[str]:
        return sorted({t.l1 for t in self._tags})

    def filter(
        self,
        l1: str | Sequence[str] | None = None,
        l2: str | Sequence[str] | None = None,
        l3: str | Sequence[str] | None = None,
    ) -> list[Tag]:
        out = self._tags
        if l1:
            keep = {l1} if isinstance(l1, str) else set(l1)
            out = [t for t in out if t.l1 in keep]
        if l2:
            keep = {l2} if isinstance(l2, str) else set(l2)
            out = [t for t in out if t.l2 in keep]
        if l3:
            keep = {l3} if isinstance(l3, str) else set(l3)
            out = [t for t in out if t.l3 in keep]
        return out

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def sample_weighted(self, k: int, pool: list[Tag] | None = None,
                        rng: random.Random | None = None) -> list[Tag]:
        """Sample k tags weighted by count (popular scenes more likely)."""
        pool = pool or self._tags
        rng = rng or random
        weights = [max(1, t.count) for t in pool]
        if len(pool) <= k:
            return list(pool)
        # Reservoir without replacement (approximate via weighted choice)
        chosen: list[Tag] = []
        idx_pool = list(range(len(pool)))
        for _ in range(k):
            i = rng.choices(idx_pool, weights=[weights[j] for j in idx_pool], k=1)[0]
            chosen.append(pool[i])
            idx_pool.remove(i)
        return chosen

    def sample_diverse(self, k: int, pool: list[Tag] | None = None,
                       rng: random.Random | None = None) -> list[Tag]:
        """Sample k tags spread across L2 categories (round-robin).

        Within each L2, pick weighted by count.
        """
        pool = pool or self._tags
        rng = rng or random
        # Group by (l1, l2)
        groups: dict[tuple[str, str], list[Tag]] = {}
        for t in pool:
            groups.setdefault((t.l1, t.l2), []).append(t)

        group_keys = list(groups.keys())
        rng.shuffle(group_keys)

        chosen: list[Tag] = []
        # Round-robin pull one from each L2 group, then continue
        round_idx = 0
        while len(chosen) < k and any(groups[g] for g in group_keys):
            for g in group_keys:
                if not groups[g]:
                    continue
                # Weighted sample within the group
                weights = [max(1, t.count) for t in groups[g]]
                t = rng.choices(groups[g], weights=weights, k=1)[0]
                groups[g].remove(t)
                chosen.append(t)
                if len(chosen) >= k:
                    break
            round_idx += 1
            if round_idx > 1000:    # safety
                break
        return chosen


# ---------------------------------------------------------------------------
# Default singleton
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def default_library() -> SceneLibrary:
    return SceneLibrary()
