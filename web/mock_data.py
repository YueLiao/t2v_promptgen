"""Mock data generators for the prototype web UI.

These stand in for real LLM calls so the entire flow is clickable
end-to-end without an API key. Replace with real phase implementations
later by routing through `core.orchestrator`.
"""
from __future__ import annotations

import random
from datetime import datetime

from ..core.schema import SL2, Axis, PromptEntry


def mock_slug_for(description: str) -> str:
    """Naive slug extraction. Real impl uses LLM."""
    d = description.lower()
    if "人手" in description or "hand" in d:
        return "human_hand"
    if "人体" in description or "body" in d:
        return "human_body"
    if "运镜" in description or "camera" in d:
        return "camera_motion"
    if "物理" in description or "physic" in d:
        return "physics"
    return "custom_capability"


_HAND_SL2_SEED = [
    ("hand_finger_count", "手指数量错误",
     "手指总数不为 5,出现多余指或缺指,并指错乱",
     ["特写", "数手指", "握"]),
    ("hand_joint_angle", "关节角度异常",
     "指关节弯曲方向或角度违反人体解剖学",
     ["拳头", "握紧", "弯曲"]),
    ("hand_grip_stability", "握持稳定性差",
     "手与所握物品的接触点游离,握持过程中物品穿插或飘移",
     ["握", "拿", "持物"]),
    ("hand_contact_fidelity", "接触贴合度差",
     "手与物体表面接触瞬间出现穿模或漂浮间隙",
     ["接触", "贴合", "按压"]),
    ("hand_motion_smoothness", "手部运动不平滑",
     "手部移动有抖动或跳跃,缺乏自然过渡",
     ["快速", "连续", "翻动"]),
    ("hand_finger_independence", "手指独立性差",
     "应当独立运动的手指出现整体联动或同时弯曲",
     ["弹奏", "敲击", "数"]),
    ("hand_occlusion_handling", "遮挡处理错误",
     "手被部分遮挡时露出的部分手指数量或位置不一致",
     ["部分遮挡", "藏在", "背后"]),
    ("hand_skin_quality", "手部皮肤质感差",
     "皮肤纹理过于光滑塑料感重,或纹理重复像贴图",
     ["特写", "高清"]),
]

_HAND_AXES_SEED = [
    ("持物角度", ["正面平举", "侧面持物", "胸前", "举过头顶"]),
    ("光照条件", ["顺光", "侧光", "逆光"]),
    ("遮挡程度", ["无遮挡", "部分遮挡"]),
    ("操作复杂度", ["单点接触", "多指协同", "双手协作"]),
]


def generate_mock_dimensions(description: str, round: int = 0,
                              feedback: str = "") -> tuple[list[SL2], list[Axis]]:
    """Return (sl2_list, axes) for the dimensions phase.

    Rounds simulate iteration — later rounds slightly tweak based on feedback.
    """
    sl2_data = list(_HAND_SL2_SEED)
    axes_data = list(_HAND_AXES_SEED)

    if round >= 2 and feedback:
        # mock: appending a feedback-derived SL2 if user mentions a keyword
        if "运动" in feedback or "速度" in feedback:
            sl2_data.append((
                "hand_speed_consistency", "手部速度一致性",
                "手部运动速度与场景节奏不匹配", ["快速", "慢动作"]
            ))

    if round >= 3:
        sl2_data = sl2_data[:7]  # mock: trim to top 7 in later rounds

    sl2_list = [
        SL2(
            id=sid,
            name=name,
            inherits_from=["物理规律与常识:人手/脸/体结构畸形"],
            description=desc,
            judging_criteria_md=f"## 判定为 Yes 的条件\n- {desc}\n\n## 判定为 No 的情况\n- 主体未出现\n- 无法判定",
            stress_keywords=kw,
        )
        for sid, name, desc, kw in sl2_data
    ]
    axes = [Axis(name=n, values=v) for n, v in axes_data]
    return sl2_list, axes


_PROMPT_TEMPLATES_ZH = [
    "{cam}{scene}一名{subject}在{light_desc}下{action_desc},手指清晰可见做出{detail_desc}",
    "{cam}{scene}一名{subject}手持{object}进行{action_desc},{light_desc},动作{detail_desc}",
    "{cam}{scene}两名{subject}双手协作{action_desc},{light_desc},手部特写,关节自然弯曲",
]

_SCENES = ["室内白色背景前", "舞台聚光灯下", "工作台前", "户外阳光草地上", "音乐厅黑色三角钢琴前", "武术馆木地板上"]
_SUBJECTS = ["年轻男子", "穿运动服女子", "穿白色练功服武者", "年轻女钢琴家", "穿衬衫男吉他手"]
_OBJECTS = ["黑色钢笔", "玻璃杯", "扑克牌", "魔方", "象棋子"]
_LIGHTS = ["顺光", "侧光", "柔和光线", "聚光灯"]
_ACTIONS = ["快速翻动手指", "慢慢转动手腕", "依次按下五指", "数手指数量", "捏起细小物品"]
_DETAILS = ["精细抓握", "快速分指", "复杂指法", "稳定握持", "多指独立运动"]
_CAMERAS = ["镜头慢慢推近,", "镜头不动,", "镜头跟随主体,", ""]
_CAMERAS_EN = ["Slow push-in. ", "Static shot. ", "Camera tracks the subject. ", ""]


def _en_prompt(zh: str) -> str:
    """Quick mock EN — production uses real LLM translation."""
    return ("[EN draft] " + zh.replace("镜头慢慢推近,", "Slow push-in. ")
                              .replace("镜头不动,", "Static shot. ")
                              .replace("镜头跟随主体,", "Camera tracks. ")
                              .replace("一名", "a ").replace("两名", "two ")
                              .replace("手指", "fingers").replace("手腕", "wrist"))


def generate_mock_prompts(sl2_list: list[SL2], axes: list[Axis],
                          target_size: int) -> list[PromptEntry]:
    """Generate target_size mock prompts evenly covering SL2 × axes."""
    random.seed(42)
    prompts = []
    sl2_ids = [s.id for s in sl2_list]
    axes_names = [a.name for a in axes]

    for i in range(target_size):
        sl2_count = random.randint(1, 3)
        sl2_covered = random.sample(sl2_ids, min(sl2_count, len(sl2_ids)))
        axes_values = {a.name: random.choice(a.values) for a in axes}

        scene = random.choice(_SCENES)
        subj = random.choice(_SUBJECTS)
        obj = random.choice(_OBJECTS)
        light = random.choice(_LIGHTS)
        action = random.choice(_ACTIONS)
        detail = random.choice(_DETAILS)
        cam_idx = random.randint(0, 3)
        cam = _CAMERAS[cam_idx]

        zh = f"{cam}{scene},一名{subj}手持{obj}{action},{light}下手指做出{detail}"
        en = (_CAMERAS_EN[cam_idx] +
              f"At {scene}, {subj} holds {obj}, performs {action} with {detail} under {light}.")

        # Difficulty heuristic
        action_count = random.randint(2, 4)
        subj_count = 1 if "两名" not in subj else 2
        from ..qa.difficulty import score, to_band
        diff_score = score(zh, action_count, subj_count, axes_values)
        band = to_band(diff_score)
        if band == "easy":
            band = "medium"  # B = 0:3:2 forbids easy

        is_stress = random.random() < 0.32  # ≈30%

        prompts.append(PromptEntry(
            id=f"spec_hand_{i+1:03d}",
            capability="human_hand",
            capability_version=1,
            difficulty=band,
            difficulty_score=diff_score,
            is_stress=is_stress,
            sl2_covered=sl2_covered,
            axes_values=axes_values,
            subject_count=subj_count,
            action_count=action_count,
            camera_zh=cam.rstrip(",").strip() if cam else None,
            camera_en=_CAMERAS_EN[cam_idx].rstrip(". ").strip() if cam_idx < 3 else None,
            prompt_zh=zh,
            prompt_en=en,
            generated_at=datetime.now(),
        ))

    # Enforce medium 60% / hard 40%
    target_medium = int(target_size * 0.6)
    target_hard = target_size - target_medium
    medium_prompts = [p for p in prompts if p.difficulty == "medium"]
    hard_prompts = [p for p in prompts if p.difficulty == "hard"]
    # rebalance by swapping
    while len(medium_prompts) < target_medium and hard_prompts:
        p = hard_prompts.pop()
        p.difficulty = "medium"
        medium_prompts.append(p)
    while len(hard_prompts) < target_hard and medium_prompts:
        p = medium_prompts.pop()
        p.difficulty = "hard"
        hard_prompts.append(p)

    return prompts


def compute_coverage_matrix(prompts: list[PromptEntry], sl2_list: list[SL2],
                            axes: list[Axis]) -> dict:
    """Compute coverage statistics for the heatmap."""
    matrix = {}
    sl2_hit_counts = {s.id: 0 for s in sl2_list}
    for p in prompts:
        for sid in p.sl2_covered:
            sl2_hit_counts[sid] = sl2_hit_counts.get(sid, 0) + 1
            for axis_name, axis_val in p.axes_values.items():
                key = f"{sid}|{axis_name}={axis_val}"
                matrix[key] = matrix.get(key, 0) + 1
    return {
        "sl2_hit_counts": sl2_hit_counts,
        "axes_cells_hit": matrix,
        "total": len(prompts),
        "difficulty": {
            "medium": sum(1 for p in prompts if p.difficulty == "medium"),
            "hard": sum(1 for p in prompts if p.difficulty == "hard"),
        },
        "stress_count": sum(1 for p in prompts if p.is_stress),
        "stress_ratio": sum(1 for p in prompts if p.is_stress) / max(1, len(prompts)),
    }
