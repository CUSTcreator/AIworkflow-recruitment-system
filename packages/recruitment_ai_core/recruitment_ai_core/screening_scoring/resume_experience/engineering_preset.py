from __future__ import annotations

from typing import Any

CATALOG_VERSION = "engineering_experience_source_v2_0"
RUBRIC_VERSION = "engineering_experience_rubric_source_v2_0"
POLICY_VERSION = "preset_experience_policy_v2_0"
PROMPT_VERSION = "preset_experience_prompt_v2_0"
SCHEMA_VERSION = "preset_experience_schema_v2_0"

FRAMEWORK_SPECS = (
    ("problem_context", "场景与问题", "判断问题是否被准确界定并对焦。"),
    ("solution_execution", "方案与执行", "判断方案适配、技术深度、可靠性和演进质量。"),
    ("outcome_value", "产出与价值", "判断验证严谨度、问题解决程度和实际价值。"),
)


def _spec(indicator_id: str, framework_id: str, name: str, ideal: str, boundary: str,
          work: list[str], project: list[str]) -> dict[str, Any]:
    return {
        "indicator_id": indicator_id, "framework_id": framework_id, "name": name,
        "ideal_definition": ideal, "activation_boundary": boundary,
        "work_unit_rubric": work, "project_rubric": project,
        "levels": work, "definition": ideal,
    }


INDICATOR_SPECS: tuple[dict[str, Any], ...] = (
    _spec("problem_definition_focus", "problem_context", "问题界定与精准对焦",
          "精准界定高价值问题，目标、范围、关键边界与成功条件一致。",
          "存在可识别的问题、需求或目标及其对应行动。",
          ["问题方向较模糊", "明确局部问题且行动基本相关", "问题、对象和主要边界清楚", "抓住关键矛盾并避免次要问题", "问题界定精准且成功条件与方案结果一致"],
          ["项目目标大致可见但核心问题不清", "明确主要需求但范围或优先关系一般", "核心问题、场景和主要边界清楚", "聚焦关键问题且目标、优先级和约束一致", "高价值问题、范围、排除项和成功条件高度一致"]),
    _spec("solution_fit_scale", "solution_execution", "方案适配与尺度控制",
          "以最低必要复杂度精准解决问题，并适配重要约束和合理演进。",
          "存在可判断方案与目标关系的事实。",
          ["处理相关但粗浅或堆砌", "方案基本可用，适配和取舍一般", "方案与问题匹配且复杂度合理", "准确把握关键约束且不过度", "以最低必要复杂度精准解决难点"],
          ["相关功能存在但组织较弱", "总体方案可用但有明显缺失或冗余", "架构与问题规模匹配且模块合理", "对规模、风险、周期和成本作出高质量取舍", "方案精准适配当前问题与合理演进"]),
    _spec("key_difficulty_resolution", "solution_execution", "问题驱动与技术突破",
          "技术由关键问题驱动，对核心瓶颈形成有效且可验证的突破。",
          "存在明确技术难点及针对性处理。",
          ["使用相关技术但问题驱动弱", "用具体方法处理常规难点", "对方法进行合理组合、调优或改造", "定位关键瓶颈并有依据改进常规方案", "形成原创或高复用的有效突破"],
          ["技术较多但与核心问题联系有限", "常规技术解决主要需求", "识别主要难点并形成针对性方案", "核心选择由关键问题驱动并形成显著突破", "突破解决核心瓶颈且边界效果可靠"]),
    _spec("mechanism_engineering_depth", "solution_execution", "实现机制与工程深度",
          "关键路径、状态、依赖和边界形成可解释、可运行的完整机制。",
          "存在具体实现对象及其工作方式。",
          ["有实现行为但方式较浅", "步骤、输入输出或直接作用基本清楚", "数据流、控制流、状态或协作清楚", "深入处理复杂依赖、状态、边界或性能", "复杂条件下机制完整清晰可靠"],
          ["实现内容主要是组件罗列", "主要模块连接并完成基本流程", "关键模块形成连贯可解释机制", "关键路径、跨模块依赖和复杂边界处理较好", "端到端机制、状态演化、依赖和边界完整"]),
    _spec("reliability_defense_depth", "solution_execution", "可靠性与纵深防御",
          "防御深度与真实风险匹配，关键失败可预防、限制和恢复。",
          "存在明确风险、失败场景或保护动作。",
          ["意识到风险但保护有限", "主要风险被单点控制", "设计与实际风险匹配且主要失败可控", "关键失败路径有深入互补保护", "防御深度与风险高度适配且不过度"],
          ["只有零散可靠性措施", "主要模块具备基本保护", "主要失败路径被合理覆盖", "多模块形成连贯纵深防御", "防御体系与风险等级高度适配并经验证"]),
    _spec("evolvability_cost_efficiency", "solution_execution", "演进性与成本效率",
          "当前复杂度克制，同时具备合理演进路径和成本效率。",
          "存在扩展、复用、迁移、容量、性能或成本事实。",
          ["有扩展或成本意识但较弱", "有具体接口、配置、抽象或单项优化", "扩展边界清楚且资源维护选择合理", "支持复用迁移或容量变化并产生收益", "实现克制且具备可逆低成本演进路径"],
          ["零散复用或成本优化", "主要模块可维护并具备基本扩展", "架构可合理演进且复杂度受控", "支持重要版本、规模或场景变化", "以最低复杂度支撑长期演进和成本效率"]),
    _spec("validation_measurement_rigor", "outcome_value", "验证与测量严谨度",
          "指标与目标一致，验证可重复、可靠且适用边界清楚。",
          "存在测试、指标、验收、反馈或结果测量。",
          ["有测试或效果判断但可靠性弱", "有明确指标、测试、验收或反馈", "测量方法与目标匹配且可核查", "覆盖关键样本、边界或失败情况", "评价可重复且结论和边界可靠"],
          ["只有结果声明或零散测试", "主要结果有基本测试或量化依据", "指标、对象和范围一致且结论可信", "多维代表性或持续验证能识别失效边界", "持续可重复验证且指标与真实目标一致"]),
    _spec("problem_resolution_completeness", "outcome_value", "问题解决充分度",
          "产出充分解决被界定的问题，已解决范围和剩余边界清楚。",
          "存在能够与问题建立关系的交付或结果。",
          ["有产出但改善有限", "解决部分局部问题", "局部问题有效解决且产出可用", "问题及重要边界均得到较好解决", "接近理想解决且剩余边界清楚"],
          ["有交付但核心问题解决程度不清", "解决部分主要需求但有明显缺口", "主要问题基本解决且产出可用", "核心问题和主要约束充分解决", "充分解决核心问题且范围限制清楚"]),
    _spec("outcome_value_applicability", "outcome_value", "产出价值与适用效应",
          "产出在目标场景持续产生高价值，或形成可广泛复用的能力。",
          "存在具体使用对象、应用场景或价值结果。",
          ["形成产出但价值弱或不清", "用于具体功能、用户或流程", "产生明确业务、用户、性能、成本或工程价值", "在重要场景产生显著价值或高质量复用", "与关键场景高度适配或具有很强复用价值"],
          ["产出存在但对象和价值模糊", "在明确场景使用或形成交付", "实际场景产生稳定价值或被复用", "有意义规模持续产生价值或多场景采用", "持续产生高价值或沉淀为广泛复用能力"]),
)


INTERVIEW_RUBRICS: dict[str, list[str]] = {
    "problem_definition_focus": [
        "只识别大致任务",
        "明确基本目标，遗漏关键约束",
        "核心问题、主要约束和成功条件基本清楚",
        "识别关键矛盾、优先级和边界，主动澄清歧义",
        "随新信息精准重构问题，范围、排除项和成功条件一致",
    ],
    "solution_fit_scale": [
        "提出相关但粗浅的处理",
        "方案基本可行，取舍或边界不足",
        "方案适配主要约束和规模，能说明主要取舍",
        "比较备选方案，以最低必要复杂度解决关键问题",
        "约束变化时仍能精准调整，支持合理演进且不过度设计",
    ],
    "key_difficulty_resolution": [
        "只罗列技术",
        "用常规方法处理明确难点",
        "定位主要瓶颈并进行针对性组合、调优或改造",
        "对常规方案提出有依据的替代或突破，说明机制和边界",
        "对核心瓶颈形成原创或高复用解法，并给出可验证路径",
    ],
    "mechanism_engineering_depth": [
        "只有浅层实现概念",
        "能说明主要组件或基本流程",
        "数据流、控制流、状态或依赖基本清楚",
        "深入处理并发、一致性、性能或关键失败路径",
        "复杂条件下的端到端机制完整、可解释且可追溯",
    ],
    "reliability_defense_depth": [
        "只提到异常或测试",
        "能处理主要单点失败",
        "针对真实风险设计预防、检测或恢复",
        "识别关键失败链，形成互补防御并说明取舍",
        "防御深度与风险高度匹配，恢复可验证且不过度设计",
    ],
    "evolvability_cost_efficiency": [
        "只声称可扩展或低成本",
        "给出配置、接口或单项资源优化",
        "扩展边界清楚，能权衡性能、资源和维护成本",
        "考虑兼容、迁移、回滚或容量变化并说明收益",
        "形成可逆、低成本的演进路径，避免无效预留",
    ],
    "validation_measurement_rigor": [
        "仅凭主观判断或单一正常样例",
        "有基本测试、指标或验收方法",
        "包含代表性样例、基线或可测量目标",
        "覆盖边界、失败和重复验证，结论可靠",
        "形成持续、可重复的验证方案，指标与真实目标和适用边界一致",
    ],
    "problem_resolution_completeness": [
        "只有局部思路，未形成闭环",
        "完成主要功能，仍有明显缺口",
        "端到端解决主要问题和关键边界",
        "核心问题充分闭环，能识别剩余风险",
        "给出完整闭环、明确验收条件和剩余边界",
    ],
    "outcome_value_applicability": [
        "只有泛化收益",
        "将产出联系到具体用户、功能或流程",
        "能说明明确的用户、业务或工程价值",
        "对重要场景价值进行可比较或可量化的评估",
        "证明持续高价值，或形成可广泛复用的能力",
    ],
}

for _indicator in INDICATOR_SPECS:
    _indicator["interview_rubric"] = INTERVIEW_RUBRICS[_indicator["indicator_id"]]


def indicators_by_id() -> dict[str, dict[str, Any]]:
    return {item["indicator_id"]: dict(item) for item in INDICATOR_SPECS}


def frameworks_by_id() -> dict[str, dict[str, Any]]:
    return {framework_id: {"framework_id": framework_id, "name": name, "definition": definition}
            for framework_id, name, definition in FRAMEWORK_SPECS}


def catalog() -> dict[str, Any]:
    indicators = [{**item, "capability_indicator_id": item["indicator_id"],
                   "rubric_version": RUBRIC_VERSION} for item in INDICATOR_SPECS]
    frameworks = [{"framework_id": framework_id, "name": name, "definition": definition,
                "indicator_ids": [item["indicator_id"] for item in INDICATOR_SPECS
                                  if item["framework_id"] == framework_id]}
               for framework_id, name, definition in FRAMEWORK_SPECS]
    return {"catalog_version": CATALOG_VERSION, "rubric_version": RUBRIC_VERSION,
            "capability_frameworks": frameworks, "capability_indicators": indicators}
