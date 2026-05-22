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
    """Naive slug extraction. Real impl uses LLM.

    Checked in order — more specific keywords first so a passing mention of
    "物理碰撞" in a temporal description doesn't get classified as physics.
    """
    d = description.lower()
    # Temporal / 时序 wins over everything else if explicit
    if "时序" in description or "temporal" in d or "动作顺序" in description \
       or "因果链" in description or "时间序列" in description:
        return "temporal_consistency"
    if "人手" in description or "hand" in d or "手指" in description:
        return "human_hand"
    if "人体" in description or "body" in d or "人物动作" in description:
        return "human_body"
    if "运镜" in description or "camera" in d or "镜头" in description:
        return "camera_motion"
    if "物理" in description or "physic" in d or "碰撞" in description or "重力" in description:
        return "physics"
    if "文字" in description or "text" in d or "招牌" in description:
        return "text_rendering"
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


_BODY_SL2_SEED = [
    ("body_proportion", "肢体比例失调", "四肢长度或躯干比例违反人体解剖", ["全身", "走路"]),
    ("body_joint_articulation", "关节弯曲异常", "膝/肘/肩关节角度违反生理范围", ["弯腰", "下蹲"]),
    ("body_balance", "重心平衡失稳", "动作过程中身体重心明显偏移到不合理位置", ["独立", "跳跃"]),
    ("body_limb_count", "肢体数量错误", "出现多/缺四肢或异常融合", ["特写"]),
    ("body_pose_consistency", "姿态时序一致性差", "前后帧姿态不连贯,有跳变", ["快速动作"]),
    ("body_motion_smoothness", "运动不平滑", "肢体移动有抖动或不自然停顿", ["跑步", "舞蹈"]),
    ("body_face_structure", "面部结构畸变", "五官位置或大小异常", ["特写", "正面"]),
]
_BODY_AXES_SEED = [
    ("视角", ["全身正面", "半身", "特写局部", "侧面"]),
    ("动作幅度", ["大幅", "中幅", "微动"]),
    ("光照", ["顺光", "侧光", "逆光"]),
    ("场景", ["室内白底", "室外自然", "舞台聚光"]),
]

_CAMERA_SL2_SEED = [
    ("camera_motion_type", "运镜类型错误", "实际运镜与 prompt 指定的类型不符", ["跟随", "推近"]),
    ("camera_motion_smoothness", "运镜不平滑", "镜头移动有抖动/卡顿/不规律加速", ["跟随主体"]),
    ("camera_speed_consistency", "运镜速度异常", "推拉速度突变或过快过慢", ["慢慢推近"]),
    ("camera_subject_locking", "主体跟随脱锁", "跟随镜头丢失主体或视点偏离", ["跟随主体"]),
    ("camera_parallax", "视差不合理", "近物远物视差不符合 3D 一致性", ["第一视角"]),
    ("camera_scene_continuity", "场景连续性破坏", "运镜中背景出现跳变或瞬移", ["拉远", "POV"]),
]
_CAMERA_AXES_SEED = [
    ("运镜类型", ["静止", "推近", "拉远", "横移", "跟随", "第一视角"]),
    ("速度", ["慢", "中", "快"]),
    ("主体距离", ["特写", "中景", "远景"]),
    ("场景复杂度", ["简单背景", "中等", "繁复城市"]),
]

_PHYSICS_SL2_SEED = [
    ("phys_gravity", "违背重力", "物体下落/抛物线轨迹错误", ["落下", "抛出"]),
    ("phys_collision", "碰撞不真实", "物体碰撞后形变/反弹不符合材质", ["撞击"]),
    ("phys_fluid", "流体异常", "液体流动/水花/烟雾形态错误", ["水流", "倒水"]),
    ("phys_cloth", "布料 / 织物物理错误", "衣服/旗帜/窗帘随风方向或重力错乱", ["飘动", "挥动"]),
    ("phys_penetration", "穿模", "物体或人物部分穿过另一物体", ["接触", "握"]),
    ("phys_friction", "摩擦表现异常", "滑动/打滑/抓地缺失或过度", ["滑动", "刹车"]),
]
_PHYSICS_AXES_SEED = [
    ("物理类型", ["固体碰撞", "流体", "布料", "颗粒", "弹性"]),
    ("速度", ["慢", "中", "快"]),
    ("尺度", ["小物体", "中型", "大型场景"]),
    ("环境", ["真空", "重力下", "水下"]),
]

_TEMPORAL_SL2_SEED = [
    ("temp_action_order", "多段动作顺序错乱",
     "三段以上动作未按 prompt 指定顺序完成,如 A→B→C 被生成成 A→C→B",
     ["先...然后", "依次", "顺序"]),
    ("temp_step_missing", "步骤缺失或跳过",
     "prompt 指定的中间动作被略过,只生成首尾或截断在首段",
     ["完整", "三段", "最后"]),
    ("temp_causal_break", "因果链断裂",
     "做完动作后应有的物理/逻辑结果未发生,如推下杯子未碎、扔球未落地",
     ["导致", "接着", "然后"]),
    ("temp_irreversibility", "连续变化的不可逆性失败",
     "应单向推进的过程出现回退,如冰融了又结回、纸折了又自动展开",
     ["融化", "燃烧", "折叠"]),
    ("temp_occlusion_consistency", "遮挡前后主体一致性",
     "主体经过遮挡物前后,衣着 / 持物 / 姿态发生突变",
     ["走过", "挡住", "再露出"]),
    ("temp_speed_fidelity", "速度感失真",
     "慢动作没慢下来、瞬间反应被拖长,或加速镜头节奏不均匀",
     ["慢动作", "瞬间", "加速"]),
    ("temp_state_memory", "状态记忆失败",
     "已发生的不可逆状态被遗忘,如倒下的人自己站起、熄灭的火自燃",
     ["已经", "保持", "继续"]),
    ("temp_sync_failure", "多主体同步失败",
     "两人需同时完成的动作(握手 / 碰杯 / 击掌)时间错位",
     ["同时", "碰杯", "击掌"]),
]

_TEMPORAL_AXES_SEED = [
    ("动作段数", ["2 段", "3 段", "4 段以上"]),
    ("因果类型", ["物理碰撞", "物体形变", "生物动作"]),
    ("镜头切换", ["单镜到底", "1 次切换"]),
    ("主体数", ["单人", "两人交互"]),
    ("速度类型", ["慢动作", "正常", "快速"]),
]

_CAPABILITY_TEMPLATES = {
    "human_hand": (_HAND_SL2_SEED, _HAND_AXES_SEED),
    "human_body": (_BODY_SL2_SEED, _BODY_AXES_SEED),
    "camera_motion": (_CAMERA_SL2_SEED, _CAMERA_AXES_SEED),
    "physics": (_PHYSICS_SL2_SEED, _PHYSICS_AXES_SEED),
    "temporal_consistency": (_TEMPORAL_SL2_SEED, _TEMPORAL_AXES_SEED),
}


# Fallback recommended_tags per capability slug — used when LLM doesn't return
# a tag suggestion (or falls back to mock dimensions). Each entry maps
# dim_code → list of value codes from annotation_schema.
_DEFAULT_RECOMMENDED_TAGS = {
    "human_hand": {
        "D1": ["S1", "S2"],
        "D2": ["A9", "A14", "A30"],
        "D3": ["V2", "V3"],
        "D4": ["C1", "C12"],
        "D5": ["F1", "F2"],
        "D8": ["G3"],
    },
    "human_body": {
        "D1": ["S1", "S2"],
        "D2": ["A1", "A6", "A10", "A17", "A22"],
        "D3": ["V2", "V3", "V4"],
        "D4": ["C1", "C4"],
        "D5": ["F3", "F4"],
        "D8": ["G2"],
    },
    "camera_motion": {
        "D1": ["S3"],
        "D4": ["C2", "C3", "C4", "C5", "C6", "C7", "C11"],
        "D5": ["F3", "F4", "F5"],
        "D6": ["E1", "E2"],
        "D8": ["G1", "G6"],
    },
    "physics": {
        "D1": ["S3"],
        "D2": ["A13", "A24", "A25", "A26", "A28"],
        "D3": ["V3", "V4", "V6"],
        "D4": ["C1", "C12"],
        "D5": ["F1", "F2", "F3"],
        "D8": ["G3"],
    },
    "temporal_consistency": {
        "D1": ["S1", "S2", "S4", "S5"],
        "D2": ["A9", "A13", "A23", "A24", "A26", "A32"],
        "D3": ["V2", "V3", "V4", "V6"],
        "D4": ["C1", "C4"],
        "D5": ["F2", "F3"],
        "D8": ["G2", "G3"],
    },
    "text_rendering": {
        "D1": ["S3"],
        "D2": ["A33"],
        "D5": ["F1", "F2"],
        "D6": ["E2", "E5", "E15"],
        "D8": ["G3"],
    },
}


def default_recommended_tags(capability_slug: str) -> dict[str, list[str]]:
    """Return a sensible fallback recommended_tags dict for a capability slug.

    Empty dict for unknown slugs.
    """
    return {k: list(v) for k, v in _DEFAULT_RECOMMENDED_TAGS.get(capability_slug, {}).items()}


def generate_mock_dimensions(description: str, round: int = 0,
                              feedback: str = "",
                              capability_slug: str = "") -> tuple[list[SL2], list[Axis]]:
    """Return (sl2_list, axes) for the dimensions phase.

    Picks template by capability slug (or detects from description).
    Rounds simulate iteration — later rounds slightly tweak based on feedback.
    """
    if not capability_slug:
        capability_slug = mock_slug_for(description)
    template_sl2, template_axes = _CAPABILITY_TEMPLATES.get(
        capability_slug, (_HAND_SL2_SEED, _HAND_AXES_SEED)
    )
    sl2_data = list(template_sl2)
    axes_data = list(template_axes)

    if round >= 2 and feedback:
        # mock: appending a feedback-derived SL2 if user mentions a keyword
        if "运动" in feedback or "速度" in feedback:
            sl2_data.append((
                "hand_speed_consistency", "手部速度一致性",
                "手部运动速度与场景节奏不匹配", ["快速", "慢动作"]
            ))

    if round >= 3:
        sl2_data = sl2_data[:7]  # mock: trim to top 7 in later rounds

    # Choose a sensible upper-dimension hint based on slug
    _INHERIT_BY_SLUG = {
        "human_hand": "物理规律与常识:人手/脸/体结构畸形",
        "human_body": "物理规律与常识:人手/脸/体结构畸形",
        "camera_motion": "指令遵循:镜头运动",
        "physics": "物理规律与常识:刚体/流体/形变",
        "temporal_consistency": "指令遵循:多步动作 + 时序",
        "text_rendering": "文字渲染:字符正确性",
    }
    inherit = _INHERIT_BY_SLUG.get(capability_slug, "指令遵循:通用")

    sl2_list = [
        SL2(
            id=sid,
            name=name,
            inherits_from=[inherit],
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
