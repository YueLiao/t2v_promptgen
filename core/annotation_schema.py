"""8-dimensional video annotation schema (D1-D8) — canonical vocabulary.

This taxonomy comes from the team's existing video annotation system
(with_annotation.json). It describes "what kind of video this prompt would
generate" along 8 orthogonal axes:

  D1 主体 (Subject)     — who/what is in the video
  D2 动作 (Action)      — what's happening
  D3 速度 (Speed)       — how fast
  D4 镜头 (Camera)      — camera motion type
  D5 景别 (Shot scale)  — how close
  D6 场景 (Scene)       — where it takes place
  D7 风格 (Style)       — visual / cinematic style
  D8 功能 (Function)    — narrative role of the shot

Used for:
  - Tagging every generated prompt with an 8-tuple (S?, A?, V?, C?, F?, E?, Y?, G?)
  - Diversity quotas (each dimension's values should be spread across a batch)
  - Alignment with the downstream annotation platform (same code → same meaning)
  - Coverage matrix analytics

Values marked with `?` in docstrings are best-effort reads from blurry
screenshots — confirm with the source system before treating them as truth.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnnotationValue:
    code: str
    name_zh: str


@dataclass(frozen=True)
class AnnotationDimension:
    code: str           # D1..D8
    name_zh: str        # 主体, 动作, ...
    prefix: str         # S, A, V, C, F, E, Y, G — value code prefix
    values: list[AnnotationValue]


# ---- D1 主体 ---------------------------------------------------------------
D1_SUBJECT = AnnotationDimension(
    code="D1", name_zh="主体", prefix="S",
    values=[
        AnnotationValue("S1", "单人角色"),
        AnnotationValue("S2", "多人角色"),
        AnnotationValue("S3", "无角色"),
        AnnotationValue("S4", "单动物/生物"),
        AnnotationValue("S5", "多主体混合"),
    ],
)

# ---- D2 动作 ---------------------------------------------------------------
D2_ACTION = AnnotationDimension(
    code="D2", name_zh="动作", prefix="A",
    values=[
        AnnotationValue("A1",  "武打格斗"),
        AnnotationValue("A2",  "武术展示"),
        AnnotationValue("A3",  "变装"),
        AnnotationValue("A4",  "变身特效"),
        AnnotationValue("A5",  "驾驶"),
        AnnotationValue("A6",  "行走"),
        AnnotationValue("A7",  "静态姿态"),
        AnnotationValue("A8",  "对白表达"),
        AnnotationValue("A9",  "物品交互"),
        AnnotationValue("A10", "奔跑冲刺"),
        AnnotationValue("A11", "坠落飞行"),
        AnnotationValue("A12", "拔枪射击"),
        AnnotationValue("A13", "物体独立运动"),
        AnnotationValue("A14", "烹饪常规"),
        AnnotationValue("A15", "无台词微表演"),
        AnnotationValue("A16", "魔法/超能力施展"),
        AnnotationValue("A17", "舞蹈表演"),
        AnnotationValue("A18", "音乐演奏/演唱"),
        AnnotationValue("A19", "亲密肢体接触"),
        AnnotationValue("A20", "情绪爆发/崩溃"),
        AnnotationValue("A21", "动物本能运动"),
        AnnotationValue("A22", "体育运动"),
        AnnotationValue("A23", "日常生活动作"),
        AnnotationValue("A24", "刚体物理运动"),
        AnnotationValue("A25", "流体/软体物理"),
        AnnotationValue("A26", "切割/破坏/形变"),
        AnnotationValue("A27", "载具/机械运动"),
        AnnotationValue("A28", "自然现象"),
        AnnotationValue("A29", "静态展示/无运动"),
        AnnotationValue("A30", "手部精细操作"),
        AnnotationValue("A31", "社交/仪式互动"),
        AnnotationValue("A32", "渐变/转换"),
        AnnotationValue("A33", "文字/UI 动效"),
        AnnotationValue("A34", "群体运动"),
        AnnotationValue("A35", "对抗/竞速"),
    ],
)

# ---- D3 速度 ---------------------------------------------------------------
D3_SPEED = AnnotationDimension(
    code="D3", name_zh="速度", prefix="V",
    values=[
        AnnotationValue("V1", "静态"),
        AnnotationValue("V2", "慢"),
        AnnotationValue("V3", "正常"),
        AnnotationValue("V4", "快"),
        AnnotationValue("V5", "极快"),
        AnnotationValue("V6", "变速"),
    ],
)

# ---- D4 镜头 ---------------------------------------------------------------
D4_CAMERA = AnnotationDimension(
    code="D4", name_zh="镜头", prefix="C",
    values=[
        AnnotationValue("C1",  "固定"),
        AnnotationValue("C2",  "推"),
        AnnotationValue("C3",  "拉"),
        AnnotationValue("C4",  "跟随"),
        AnnotationValue("C5",  "摇"),
        AnnotationValue("C6",  "环绕"),
        AnnotationValue("C7",  "POV"),
        AnnotationValue("C8",  "FPV 无人机"),
        AnnotationValue("C9",  "手持晃动"),
        AnnotationValue("C10", "复合"),
        AnnotationValue("C11", "航拍/鸟瞰"),
        AnnotationValue("C12", "微距/极近"),
        AnnotationValue("C13", "未指定/推断"),
    ],
)

# ---- D5 景别 (best-effort from blurry screenshot; confirm with source) -----
D5_SHOT_SCALE = AnnotationDimension(
    code="D5", name_zh="景别", prefix="F",
    values=[
        AnnotationValue("F1", "特写"),
        AnnotationValue("F2", "近景"),
        AnnotationValue("F3", "中景"),
        AnnotationValue("F4", "全景"),
        AnnotationValue("F5", "远景"),
        AnnotationValue("F6", "景别变化"),
    ],
)

# ---- D6 场景 ---------------------------------------------------------------
D6_SCENE = AnnotationDimension(
    code="D6", name_zh="场景", prefix="E",
    values=[
        AnnotationValue("E1",  "室外自然"),
        AnnotationValue("E2",  "室外城市"),
        AnnotationValue("E3",  "赛道"),
        AnnotationValue("E4",  "室内古建"),
        AnnotationValue("E5",  "室内现代"),
        AnnotationValue("E6",  "舞台会场"),
        AnnotationValue("E7",  "室外历史"),
        AnnotationValue("E8",  "奇幻虚拟"),
        AnnotationValue("E9",  "车内"),
        AnnotationValue("E10", "空中"),
        AnnotationValue("E11", "水下"),
        AnnotationValue("E12", "太空/外星"),
        AnnotationValue("E13", "实验室/医疗"),
        AnnotationValue("E14", "教育/办公"),
        AnnotationValue("E15", "商业/餐饮"),
        AnnotationValue("E16", "运动场/游乐场"),
        AnnotationValue("E17", "微观/集体"),       # ? confirm
        AnnotationValue("E18", "抽象/纯色背景"),
    ],
)

# ---- D7 风格 ---------------------------------------------------------------
D7_STYLE = AnnotationDimension(
    code="D7", name_zh="风格", prefix="Y",
    values=[
        AnnotationValue("Y1",  "写实电影"),        # ? confirm
        AnnotationValue("Y2",  "武侠功夫"),        # ? confirm
        AnnotationValue("Y3",  "功夫烹饪"),
        AnnotationValue("Y4",  "写真光影"),        # ? confirm
        AnnotationValue("Y5",  "古风"),
        AnnotationValue("Y6",  "韦斯安德森"),
        AnnotationValue("Y7",  "奇幻超英"),
        AnnotationValue("Y8",  "油画印象派"),
        AnnotationValue("Y9",  "搞笑猎奇"),
        AnnotationValue("Y10", "梦幻古风"),
        AnnotationValue("Y11", "复古邵氏"),
        AnnotationValue("Y12", "毛毡工艺"),
        AnnotationValue("Y13", "动画/卡通"),
        AnnotationValue("Y14", "3D 渲染/CG"),
        AnnotationValue("Y15", "科幻赛博"),
        AnnotationValue("Y16", "纪录片/自然"),
        AnnotationValue("Y17", "广告/产品"),
        AnnotationValue("Y18", "像素/复古游戏"),
        AnnotationValue("Y19", "水彩/手绘"),
        AnnotationValue("Y20", "写实/无风格化"),
    ],
)

# ---- D8 功能 ---------------------------------------------------------------
D8_FUNCTION = AnnotationDimension(
    code="D8", name_zh="功能", prefix="G",
    values=[
        AnnotationValue("G1", "建立"),
        AnnotationValue("G2", "动作"),
        AnnotationValue("G3", "细节"),
        AnnotationValue("G4", "反应"),
        AnnotationValue("G5", "收尾"),
        AnnotationValue("G6", "转场"),
    ],
)


ALL_DIMENSIONS: list[AnnotationDimension] = [
    D1_SUBJECT, D2_ACTION, D3_SPEED, D4_CAMERA,
    D5_SHOT_SCALE, D6_SCENE, D7_STYLE, D8_FUNCTION,
]

# Flat lookup: code → (dimension, AnnotationValue). Used by validators.
CODE_INDEX: dict[str, tuple[AnnotationDimension, AnnotationValue]] = {
    v.code: (d, v) for d in ALL_DIMENSIONS for v in d.values
}


def is_valid_code(code: str) -> bool:
    return code in CODE_INDEX


def name_of(code: str) -> str | None:
    """Return Chinese name for a code, or None if unknown."""
    if code not in CODE_INDEX:
        return None
    return CODE_INDEX[code][1].name_zh


def format_for_llm() -> str:
    """Render the full taxonomy as a compact block for inclusion in LLM prompts."""
    lines = []
    for d in ALL_DIMENSIONS:
        items = ", ".join(f"{v.code} {v.name_zh}" for v in d.values)
        lines.append(f"{d.code} {d.name_zh} ({d.prefix}): {items}")
    return "\n".join(lines)


def validate_recommended_tags(rec: dict[str, list[str]]) -> dict[str, list[str]]:
    """Filter recommended_tags dict: drop unknown codes, ensure dim alignment.

    Returns a cleaned version safe to store on Run.
    """
    out: dict[str, list[str]] = {}
    for dim_code, codes in (rec or {}).items():
        valid = []
        for c in codes or []:
            if c in CODE_INDEX and CODE_INDEX[c][0].code == dim_code:
                valid.append(c)
        if valid:
            out[dim_code] = valid
    return out


def validate_8tuple(tup: dict[str, str]) -> list[str]:
    """Return list of error strings; empty list = valid.

    Expected keys: D1, D2, D3, D4, D5, D6, D7, D8.
    Each value must be a valid code starting with that dimension's prefix.
    """
    errors: list[str] = []
    for d in ALL_DIMENSIONS:
        v = tup.get(d.code)
        if not v:
            errors.append(f"{d.code} ({d.name_zh}) missing")
            continue
        if v not in CODE_INDEX:
            errors.append(f"{d.code}={v!r} not a known code")
            continue
        actual_dim, _ = CODE_INDEX[v]
        if actual_dim.code != d.code:
            errors.append(
                f"{d.code}={v!r} belongs to {actual_dim.code} not {d.code}"
            )
    return errors


# ---------------------------------------------------------------------------
# Counts (for sanity / display)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    total = 0
    for d in ALL_DIMENSIONS:
        print(f"{d.code} {d.name_zh:6s} ({d.prefix}): {len(d.values):>3d} 项")
        total += len(d.values)
    print(f"{'合计':>14s}: {total} 项")
