"""12 preset rewrite-card specifications.

Each card has:
  - id: matches TransformId in core/rewrite_schema.py
  - name_zh: human label (shown in UI button + diff log)
  - description: one-line UI hint
  - group: 5 visual groups in R2 page (subject / scene / temporal / action / difficulty)
  - params: list of parameter specs (enum or free input)
  - prompt_fragment: how this card is rendered into the LLM system prompt;
    a Python format string that takes the user's params as kwargs

Grouped UI order:
  主体类  ─ subject_swap, add_interaction, add_micro_action
  场景类  ─ scene_shift, style_apply, camera_set
  时序类  ─ add_temporal, add_causal_chain, add_irreversibility
  动作类  ─ action_chain_extend, speed_adjust
  难度类  ─ difficulty_up
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ParamSpec:
    key: str
    type: Literal["enum", "int", "text"]
    label_zh: str
    default: str | int
    options: list[str] | None = None     # for enum
    min: int | None = None
    max: int | None = None


@dataclass(frozen=True)
class CardSpec:
    id: str
    name_zh: str
    description_zh: str
    group: Literal["主体类", "场景类", "时序类", "动作类", "难度类"]
    params: list[ParamSpec] = field(default_factory=list)
    prompt_fragment: str = ""


# =========================================================================
# 主体类
# =========================================================================

SUBJECT_SWAP = CardSpec(
    id="subject_swap",
    name_zh="主体替换",
    description_zh="把 prompt 里的主体换成另一类(单人 → 多人,人 → 动物 等)",
    group="主体类",
    params=[
        ParamSpec("from_type", "enum", "原主体类型", "S1",
                  options=["S1 单人", "S2 多人", "S3 无角色", "S4 单动物", "S5 多主体"]),
        ParamSpec("to_type", "enum", "改成", "S2",
                  options=["S1 单人", "S2 多人", "S3 无角色", "S4 单动物", "S5 多主体"]),
    ],
    prompt_fragment=(
        "把原 prompt 里的主体从 [{from_type}] 换成 [{to_type}]。"
        "如果原 prompt 没有明确主体,按 to_type 添加合理的主体。"
    ),
)

ADD_INTERACTION = CardSpec(
    id="add_interaction",
    name_zh="加主体交互",
    description_zh="给单主体场景加上和另一个主体/物体的交互动作",
    group="主体类",
    params=[
        ParamSpec("target", "enum", "交互对象", "人",
                  options=["人", "物体", "动物", "自然元素"]),
    ],
    prompt_fragment=(
        "如果原 prompt 是单主体动作,加入与 [{target}] 的具体交互"
        "(伸手、握住、推动、追逐、对话等)。让画面变成双主体协作或博弈。"
    ),
)

ADD_MICRO_ACTION = CardSpec(
    id="add_micro_action",
    name_zh="加微动作/微表情",
    description_zh="补充微小但可见的细节动作,提升真实感",
    group="主体类",
    params=[
        ParamSpec("focus", "enum", "细节聚焦", "全部",
                  options=["面部", "手部", "眼神", "呼吸", "全部"]),
    ],
    prompt_fragment=(
        "在主体动作之外补充 [{focus}] 类的微动作或微表情"
        "(眨眼、皱眉、手指轻敲、呼吸起伏等),让画面更可信。"
    ),
)


# =========================================================================
# 场景类
# =========================================================================

SCENE_SHIFT = CardSpec(
    id="scene_shift",
    name_zh="场景迁移",
    description_zh="把场景换成 D6 词表里指定的一项",
    group="场景类",
    params=[
        ParamSpec("target_scene", "enum", "目标场景", "E1 室外自然",
                  options=[
                      "E1 室外自然", "E2 室外城市", "E3 赛道", "E4 室内古建",
                      "E5 室内现代", "E6 舞台会场", "E7 室外历史", "E8 奇幻虚拟",
                      "E9 车内", "E10 空中", "E11 水下", "E12 太空/外星",
                      "E13 实验室/医疗", "E14 教育/办公", "E15 商业/餐饮",
                      "E16 运动场/游乐场", "E17 微观/集体", "E18 抽象/纯色背景",
                  ]),
        ParamSpec("preserve_action", "enum", "保留原动作", "是",
                  options=["是", "否"]),
    ],
    prompt_fragment=(
        "把场景换成 [{target_scene}]。preserve_action=[{preserve_action}],"
        "为是时尽量保留原动作只换背景,为否时也可顺势调整动作以契合新场景。"
    ),
)

STYLE_APPLY = CardSpec(
    id="style_apply",
    name_zh="风格转换",
    description_zh="应用 D7 视觉风格,写进 prompt 开头",
    group="场景类",
    params=[
        ParamSpec("target_style", "enum", "目标风格", "Y20 写实/无风格化",
                  options=[
                      "Y1 写实电影", "Y2 武侠功夫", "Y3 功夫烹饪", "Y4 写真光影",
                      "Y5 古风", "Y6 韦斯安德森", "Y7 奇幻超英", "Y8 油画印象派",
                      "Y9 搞笑猎奇", "Y10 梦幻古风", "Y11 复古邵氏", "Y12 毛毡工艺",
                      "Y13 动画/卡通", "Y14 3D 渲染/CG", "Y15 科幻赛博",
                      "Y16 纪录片/自然", "Y17 广告/产品", "Y18 像素/复古游戏",
                      "Y19 水彩/手绘", "Y20 写实/无风格化",
                  ]),
    ],
    prompt_fragment=(
        "把改写后的 prompt 开头加上 [{target_style}] 风格的明确描述"
        "(色调、构图、质感、镜头语言等),让 T2V 模型清晰接到风格信号。"
    ),
)

CAMERA_SET = CardSpec(
    id="camera_set",
    name_zh="镜头切换",
    description_zh="应用 D4 镜头运动方式",
    group="场景类",
    params=[
        ParamSpec("target_camera", "enum", "目标镜头", "C1 固定",
                  options=[
                      "C1 固定", "C2 推", "C3 拉", "C4 跟随", "C5 摇",
                      "C6 环绕", "C7 POV", "C8 FPV 无人机", "C9 手持晃动",
                      "C10 复合", "C11 航拍/鸟瞰", "C12 微距/极近", "C13 未指定/推断",
                  ]),
    ],
    prompt_fragment=(
        "把改写后的 prompt **以镜头说明开头**,形式为 [{target_camera}],"
        "如 '镜头慢慢推近,...' / '航拍俯视,...'。"
    ),
)


# =========================================================================
# 时序类(新加 3 张)
# =========================================================================

ADD_TEMPORAL = CardSpec(
    id="add_temporal",
    name_zh="加时序段数",
    description_zh="把单一动作扩成多段顺序动作(先 X 然后 Y 最后 Z)",
    group="时序类",
    params=[
        ParamSpec("segments", "enum", "段数", "3 段",
                  options=["2 段", "3 段", "4 段以上"]),
    ],
    prompt_fragment=(
        "把原动作扩成 [{segments}] 顺序明确的子动作,用'先…然后…最后…'"
        "或'起初…接着…紧接着…'连起来,让 5 秒视频里能看出时序展开。"
    ),
)

ADD_CAUSAL_CHAIN = CardSpec(
    id="add_causal_chain",
    name_zh="加因果链",
    description_zh="给动作加上自然应有的结果,测因果完整性",
    group="时序类",
    params=[
        ParamSpec("chain_depth", "enum", "因果深度", "2 层",
                  options=["1 层", "2 层", "3 层"]),
    ],
    prompt_fragment=(
        "在原动作之后追加 [{chain_depth}] 层因果结果:动作 → 直接结果 → 次级影响。"
        "如'推下杯子 → 杯子摔在地板上碎裂 → 碎片四散'。"
    ),
)

ADD_IRREVERSIBILITY = CardSpec(
    id="add_irreversibility",
    name_zh="加不可逆变化",
    description_zh="加入物理/化学的单向变化,测连续不可逆性",
    group="时序类",
    params=[
        ParamSpec("type", "enum", "变化类型", "融化",
                  options=["融化", "燃烧", "断裂", "凝固", "腐烂", "蒸发", "崩塌"]),
    ],
    prompt_fragment=(
        "加入 [{type}] 类型的不可逆过程,从初始状态推进到不可恢复状态,"
        "明写中间渐变(逐渐、慢慢、最后)。"
    ),
)


# =========================================================================
# 动作类
# =========================================================================

ACTION_CHAIN_EXTEND = CardSpec(
    id="action_chain_extend",
    name_zh="动作链扩展",
    description_zh="把单动作分解成 N 个连续子动作",
    group="动作类",
    params=[
        ParamSpec("n_actions", "enum", "子动作数", "3",
                  options=["2", "3", "4"]),
    ],
    prompt_fragment=(
        "把原 prompt 里的主动作分解成 [{n_actions}] 个**连续、明确、可视化**"
        "的子动作。如'开门' → '伸手握住把手 → 转动一圈 → 推开门'。"
    ),
)

SPEED_ADJUST = CardSpec(
    id="speed_adjust",
    name_zh="速度调整",
    description_zh="把动作改写成 D3 指定速度",
    group="动作类",
    params=[
        ParamSpec("target_speed", "enum", "目标速度", "V3 正常",
                  options=["V1 静态", "V2 慢", "V3 正常", "V4 快", "V5 极快", "V6 变速"]),
    ],
    prompt_fragment=(
        "把主动作的速度感写成 [{target_speed}]。"
        "慢用'缓慢/慢慢',快用'迅速/急/突然',变速用'先慢后快/由慢转急'。"
    ),
)


# =========================================================================
# 难度类
# =========================================================================

DIFFICULTY_UP = CardSpec(
    id="difficulty_up",
    name_zh="难度提升",
    description_zh="综合提升 prompt 难度(多步 / 多主体 / 物理细节 / 遮挡 等)",
    group="难度类",
    params=[
        ParamSpec("level", "enum", "提升等级", "+2",
                  options=["+1", "+2", "+3"]),
        ParamSpec("prefer", "enum", "侧重方向", "全部",
                  options=["时序", "多主体", "物理细节", "遮挡", "全部"]),
    ],
    prompt_fragment=(
        "综合提升难度 [{level}],侧重 [{prefer}]。可加多步动作、多主体交互、"
        "物理细节(穿模/碰撞/反弹)、遮挡过渡、速度变化等。原文越简单越要大幅扩写。"
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_CARDS: list[CardSpec] = [
    # 主体类
    SUBJECT_SWAP, ADD_INTERACTION, ADD_MICRO_ACTION,
    # 场景类
    SCENE_SHIFT, STYLE_APPLY, CAMERA_SET,
    # 时序类
    ADD_TEMPORAL, ADD_CAUSAL_CHAIN, ADD_IRREVERSIBILITY,
    # 动作类
    ACTION_CHAIN_EXTEND, SPEED_ADJUST,
    # 难度类
    DIFFICULTY_UP,
]

CARDS_BY_ID: dict[str, CardSpec] = {c.id: c for c in ALL_CARDS}

GROUPS = ["主体类", "场景类", "时序类", "动作类", "难度类"]


def card_for(card_id: str) -> CardSpec | None:
    return CARDS_BY_ID.get(card_id)


def cards_in_group(group: str) -> list[CardSpec]:
    return [c for c in ALL_CARDS if c.group == group]


def render_card(card: CardSpec, params: dict) -> str:
    """Render a card's prompt fragment with user-chosen params.

    Missing params fall back to defaults. Unknown keys are silently ignored.
    """
    p = {ps.key: params.get(ps.key, ps.default) for ps in card.params}
    return card.prompt_fragment.format(**p)


def cards_to_ui_dict() -> dict:
    """Serialize all cards for the frontend (Alpine consumes this)."""
    return {
        "groups": [
            {
                "name": g,
                "cards": [
                    {
                        "id": c.id,
                        "name_zh": c.name_zh,
                        "description_zh": c.description_zh,
                        "params": [
                            {
                                "key": p.key,
                                "type": p.type,
                                "label_zh": p.label_zh,
                                "default": p.default,
                                "options": p.options,
                            }
                            for p in c.params
                        ],
                    }
                    for c in cards_in_group(g)
                ],
            }
            for g in GROUPS
        ]
    }
