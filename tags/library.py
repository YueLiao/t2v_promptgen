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

    def sample_for_capability(
        self,
        capability_slug: str,
        k: int,
        diversity_floor: float = 0.3,
        rng: random.Random | None = None,
    ) -> list[Tag]:
        """Sample tags adapted to a specific capability.

        Mixes:
            (1-diversity_floor) k tags drawn from capability-preferred L1/L2
                                weighted by affinity × count
            diversity_floor × k tags drawn diversely across all L2s
                                (keeps long-tail coverage)

        Unknown capabilities → falls back to pure diverse sampling.
        """
        rng = rng or random
        affinity = CAPABILITY_AFFINITY.get(capability_slug)
        if not affinity:
            return self.sample_diverse(k, rng=rng)

        k_targeted = max(1, int(k * (1 - diversity_floor)))
        k_diverse = k - k_targeted

        # ----- Targeted pool -----
        # Bucket-level sampling: pick (L1, L2) buckets with prob ∝ affinity,
        # then pick a tag inside the bucket weighted by count.
        # This decouples "is this bucket relevant to the capability" from
        # "does this bucket happen to have lots of tags".
        l2_buckets: dict[tuple[str, str], list[Tag]] = {}
        for t in self._tags:
            l2_buckets.setdefault((t.l1, t.l2), []).append(t)

        bucket_keys: list[tuple[str, str]] = []
        bucket_weights: list[float] = []
        for (l1, l2), tags in l2_buckets.items():
            # Synthetic tag for affinity lookup
            w = _affinity_weight(Tag(l1=l1, l2=l2, l3="", l4=None), affinity)
            if w > 0:
                bucket_keys.append((l1, l2))
                bucket_weights.append(w)

        targeted: list[Tag] = []
        used_ids: set[int] = set()
        attempts = 0
        max_attempts = k_targeted * 8
        while len(targeted) < k_targeted and attempts < max_attempts and bucket_keys:
            attempts += 1
            bucket = rng.choices(bucket_keys, weights=bucket_weights, k=1)[0]
            available = [t for t in l2_buckets[bucket] if id(t) not in used_ids]
            if not available:
                continue
            count_weights = [max(1, t.count) for t in available]
            t = rng.choices(available, weights=count_weights, k=1)[0]
            targeted.append(t)
            used_ids.add(id(t))

        # Diverse pool excluding already-picked
        picked_ids = {(t.l1, t.l2, t.l3, t.l4) for t in targeted}
        remaining = [t for t in self._tags
                     if (t.l1, t.l2, t.l3, t.l4) not in picked_ids]
        diverse = self.sample_diverse(k_diverse, pool=remaining, rng=rng) if k_diverse else []

        out = targeted + diverse
        rng.shuffle(out)
        return out[:k]

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
# Capability ↔ scene affinity (manually curated; v0.8)
# ---------------------------------------------------------------------------
#
# Weight tables: (l1, l2 or "*") -> weight multiplier
# "*" matches any L2 within that L1.
# A tag without a matching key gets weight 0 (excluded from targeted pool).
#
# Mental model: "for THIS capability, which (L1, L2) buckets are likely to
# produce videos where the failure modes of THIS capability are testable?"
#
# Adding a new capability: register its affinity here, or pass affinity dict
# via SceneLibrary.sample_for_capability(... affinity=...) (future).

CAPABILITY_AFFINITY: dict[str, dict[tuple[str, str], float]] = {
    # --- 人手生成 ---
    "human_hand": {
        ("人类活动场景", "食物"):              5.0,    # 烹饪饮食 — 手部高度可见
        ("人类活动场景", "艺术和手工艺品"):    5.0,    # 缝纫陶艺木雕
        ("人类活动场景", "音乐和戏剧"):        4.5,    # 乐器演奏
        ("人类活动场景", "时尚与美容护理"):    4.0,    # 化妆刷牙
        ("人类活动场景", "体育"):              3.0,
        ("人类活动场景", "日常活动"):          3.0,
        ("人类活动场景", "*"):                 2.0,
        ("常见事物", "生活用品"):              3.0,    # 持物
        ("常见事物", "专业工具"):              4.0,    # 握工具
        ("常见事物", "*"):                     1.5,
    },

    # --- 人体生成 ---
    "human_body": {
        ("人类活动场景", "体育"):              5.0,    # 大幅肢体动作
        ("人类活动场景", "舞蹈"):              5.0,
        ("人类活动场景", "娱乐"):              3.5,
        ("人类活动场景", "音乐和戏剧"):        3.5,
        ("人类活动场景", "*"):                 2.5,
        ("室内环境", "公共场所室内"):          2.0,
    },

    # --- 运镜稳定性 ---
    "camera_motion": {
        ("城市建筑", "公共场所室外"):          5.0,    # 街道广场建筑
        ("城市建筑", "交通建筑"):              3.5,
        ("自然风景", "地质环境和现象"):        3.5,    # 山景纵深
        ("自然风景", "水体环境和现象"):        2.5,
        ("自然风景", "*"):                     2.0,
        ("室内环境", "*"):                     2.5,    # 长廊楼梯室内
        ("交通工具", "*"):                     2.0,    # POV
        ("人类活动场景", "体育"):              2.5,    # 跟随运动
    },

    # --- 物理仿真 ---
    "physics": {
        ("自然风景", "水体环境和现象"):        4.5,    # 流体
        ("自然风景", "气候现象"):              3.5,    # 风/雨/雪
        ("自然风景", "自然现象"):              3.0,
        ("常见事物", "生活用品"):              3.0,    # 碰撞/掉落
        ("常见事物", "基础设施"):              2.5,
        ("人类活动场景", "体育"):              3.0,    # 球类抛物线
        ("动物活动场景", "*"):                 2.5,    # 动物运动
    },

    # --- 文本生成(招牌/书法/路标) ---
    "text_rendering": {
        ("城市建筑", "公共场所室外"):          4.0,    # 招牌
        ("室内环境", "*"):                     3.5,    # 店内
        ("常见事物", "生活用品"):              3.0,
        ("人类活动场景", "艺术和手工艺品"):    4.5,    # 书法绘画
        ("人类活动场景", "日常活动"):          2.5,
    },

    # --- 审美 / 风格 ---
    "aesthetic": {
        ("自然风景", "*"):                     4.0,
        ("城市建筑", "*"):                     3.0,
        ("人类活动场景", "*"):                 2.5,
        ("动物活动场景", "*"):                 2.5,
        ("超现实场景", "*"):                   3.0,
    },

    # --- 情绪 / 表演 ---
    "emotion": {
        ("人类活动场景", "音乐和戏剧"):        4.5,
        ("人类活动场景", "日常活动"):          4.0,
        ("人类活动场景", "舞蹈"):              3.5,
        ("人类活动场景", "*"):                 3.0,
    },

    # --- 动物生成 ---
    "animal": {
        ("动物活动场景", "*"):                 5.0,
        ("自然风景", "*"):                     2.5,
        ("人类活动场景", "*"):                 1.5,
    },

    # --- 流体 / 烟雾 / 火焰 ---
    "fluid_dynamics": {
        ("自然风景", "水体环境和现象"):        5.0,
        ("自然风景", "气候现象"):              4.0,
        ("自然风景", "自然现象"):              3.5,
        ("常见事物", "生活用品"):              2.5,
        ("人类活动场景", "食物"):              2.5,    # 倒水 / 翻炒油烟
    },
}


def _affinity_weight(tag: Tag, affinity: dict[tuple[str, str], float]) -> float:
    """Return weight multiplier for tag under the given affinity table.

    Precedence: (L1, L2) exact > (L1, "*") wildcard > 0.
    """
    if (tag.l1, tag.l2) in affinity:
        return affinity[(tag.l1, tag.l2)]
    if (tag.l1, "*") in affinity:
        return affinity[(tag.l1, "*")]
    return 0.0


# ---------------------------------------------------------------------------
# Default singleton
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def default_library() -> SceneLibrary:
    return SceneLibrary()
