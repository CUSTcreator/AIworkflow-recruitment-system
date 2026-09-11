from __future__ import annotations

from copy import deepcopy
from typing import Any

from .engineering_preset import FRAMEWORK_SPECS, INDICATOR_SPECS as LEGACY_ENGINEERING_SPECS

DEFAULT_MODEL_ID = "engineering_experience"
DEFAULT_MODEL_VERSION = "1.0"
MODEL_CATALOG_VERSION = "preset_experience_catalog_v2_0"
MODEL_RUBRIC_VERSION = "preset_experience_rubric_v2_0"
MODEL_POLICY_VERSION = "preset_experience_policy_v2_0"
MODEL_PROMPT_VERSION = "preset_experience_prompt_v2_1"


def _indicator(
    indicator_id: str,
    framework_id: str,
    name: str,
    ideal_definition: str,
    activation_boundary: str,
    levels: list[str],
) -> dict[str, Any]:
    return {
        "indicator_id": indicator_id,
        "capability_indicator_id": indicator_id,
        "framework_id": framework_id,
        "name": name,
        "definition": ideal_definition,
        "ideal_definition": ideal_definition,
        "activation_boundary": activation_boundary,
        "work_unit_rubric": levels,
        "project_rubric": levels,
        "interview_rubric": levels,
        "levels": levels,
        "rubric_version": MODEL_RUBRIC_VERSION,
    }


def _engineering_specs() -> tuple[dict[str, Any], ...]:
    specs = deepcopy(LEGACY_ENGINEERING_SPECS)
    for item in specs:
        if item["indicator_id"] == "key_difficulty_resolution":
            item["indicator_id"] = "key_difficulty_resolution"
            item["capability_indicator_id"] = "key_difficulty_resolution"
            item["name"] = "问题驱动与关键难点解决"
            item["ideal_definition"] = "准确定位关键难点并形成有效、可解释、可验证的解决。"
            item["definition"] = item["ideal_definition"]
            item["work_unit_rubric"] = [
                "使用相关方法，但与实际难点联系较弱",
                "使用常规方法处理明确局部难点",
                "针对局部难点合理组合、调优或改造方法",
                "准确定位关键瓶颈并作出有依据的改进或取舍",
                "对核心瓶颈形成高度适配、可靠且可验证的解决",
            ]
            item["project_rubric"] = [
                "工作内容较多，但关键难点不清楚",
                "识别主要难点并完成基本处理",
                "关键难点得到有效、可解释的针对性处理",
                "核心难点被高质量解决，方法、依据和边界清楚",
                "核心难点在复杂约束下被充分、可靠且不过度地解决",
            ]
            item["interview_rubric"] = [
                "只罗列方法或现象",
                "用常规方法处理明确难点",
                "定位主要瓶颈并进行针对性组合、调优或改造",
                "对关键瓶颈提出有依据的改进或取舍并说明边界",
                "在复杂约束下形成高度适配、可靠且可验证的解决",
            ]
            item["levels"] = item["work_unit_rubric"]
        item["rubric_version"] = MODEL_RUBRIC_VERSION
    return tuple(specs)


GENERAL_PROFESSIONAL_SPECS: tuple[dict[str, Any], ...] = (
    _indicator("goal_problem_definition", "problem_context", "目标与问题界定",
        "准确识别工作目标、核心问题、范围和成功条件。",
        "存在可识别的目标、问题或任务及对应行动。",
        ["只知道大致任务", "明确基本目标但范围较模糊", "目标、主要问题和边界基本清楚", "准确识别关键矛盾、优先级和成功条件", "在复杂信息下仍能精准界定高价值目标及边界"]),
    _indicator("information_basis_identification", "problem_context", "信息依据与关键因素识别",
        "使用充分、相关、可靠的信息识别关键因素并形成判断。",
        "存在信息收集、核验、分析、比较或判断事实。",
        ["主要凭直接指令或直觉", "收集基本信息但核验和筛选不足", "使用相关信息识别主要因素并形成合理判断", "比较多源信息并识别偏差、缺口和关键因素", "建立充分、可靠、可追溯的判断依据并明确边界"]),
    _indicator("approach_fit", "solution_execution", "路径设计与适配",
        "执行路径与目标、约束和实际场景精准适配。",
        "存在可判断行动路径与目标关系的事实。",
        ["行动相关但零散", "路径基本可行但取舍或边界不足", "路径与目标、约束和场景基本匹配", "比较备选路径并作出高质量取舍", "以最低必要复杂度形成精准、可调整且不过度的路径"]),
    _indicator("execution_coordination", "solution_execution", "执行推进与协同",
        "有序推进任务，协调相关方并处理依赖和变化。",
        "存在推进、分工、协作、沟通或跨环节处理事实。",
        ["被动完成局部动作", "能推进本人任务但协同较弱", "能组织主要步骤并协调关键相关方", "主动处理依赖、变化和冲突以保证整体推进", "在复杂协作中形成清晰机制并稳定推动闭环"]),
    _indicator("risk_compliance_quality", "solution_execution", "风险合规与质量控制",
        "识别并控制关键风险，过程符合规则且质量可控。",
        "存在风险、规则、审核、质量或纠偏事实。",
        ["仅处理已出现问题", "有基本检查或规则意识", "主要风险、规则和质量要求得到合理控制", "识别关键风险并设置预防、复核和纠偏措施", "风险控制与任务等级高度适配、可追溯且不过度"]),
    _indicator("resource_cost_efficiency", "solution_execution", "资源成本与效率",
        "在时间、人力、预算和资源约束下取得合理效率。",
        "存在进度、资源、预算、成本或效率事实。",
        ["只关注完成任务", "有单项时间或成本意识", "能合理安排主要时间、人力、预算或资源", "在多项约束下优化投入产出并说明取舍", "形成稳定、高效、可持续且可验证的资源配置方式"]),
    _indicator("result_accuracy_validation", "outcome_value", "结果准确与验证",
        "通过核验、反馈或数据确认结果准确可靠。",
        "存在复核、验收、反馈、指标或结果测量。",
        ["仅有主观结果声明", "有基本复核、反馈或数据", "验证方式与目标基本匹配且结果可核查", "多来源或多环节验证覆盖关键边界", "形成持续、可靠、可重复的验证和纠偏闭环"]),
    _indicator("goal_closure", "outcome_value", "目标达成与闭环",
        "产出充分完成目标，遗留问题和后续处理清楚。",
        "存在能够与目标建立关系的交付或结果。",
        ["只有局部产出", "完成部分目标但仍有明显缺口", "主要目标完成且结果可使用或验收", "核心目标及重要边界充分闭环", "充分完成目标并清楚说明遗留问题、边界和后续处理"]),
    _indicator("outcome_value_reusability", "outcome_value", "产出价值与复用沉淀",
        "产出产生明确价值，或沉淀为可复用的流程、方法或资产。",
        "存在具体使用对象、应用场景、收益或沉淀结果。",
        ["有产出但价值不清", "服务具体对象或流程", "产生明确业务、组织、成本或质量价值", "在重要场景产生显著价值或形成可复用方法", "持续产生高价值或沉淀为广泛复用的流程、标准或资产"]),
)

MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    "engineering_experience": {
        "model_id": "engineering_experience",
        "version": "1.0",
        "name": "工程技术经历",
        "indicators": _engineering_specs(),
    },
    "general_professional_experience": {
        "model_id": "general_professional_experience",
        "version": "1.0",
        "name": "通用职业经历",
        "indicators": GENERAL_PROFESSIONAL_SPECS,
    },
}


def get_preset_model(
    model_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    resolved_id = str(model_id or DEFAULT_MODEL_ID)
    if resolved_id not in MODEL_CONFIGS:
        raise ValueError(f"unknown_preset_model:{resolved_id}")
    stored = MODEL_CONFIGS[resolved_id]
    resolved_version = str(version or stored["version"])
    if resolved_version != stored["version"]:
        raise ValueError(
            f"unsupported_preset_model_version:{resolved_id}:{resolved_version}"
        )
    indicators = tuple(deepcopy(stored["indicators"]))
    frameworks = tuple({
        "framework_id": framework_id,
        "name": name,
        "definition": definition,
        "indicator_ids": [
            item["indicator_id"]
            for item in indicators
            if item["framework_id"] == framework_id
        ],
    } for framework_id, name, definition in FRAMEWORK_SPECS)
    return {**stored, "indicators": indicators, "frameworks": frameworks}


def preset_catalog(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "catalog_version": MODEL_CATALOG_VERSION,
        "rubric_version": MODEL_RUBRIC_VERSION,
        "preset_model_id": model["model_id"],
        "preset_model_version": model["version"],
        "preset_model_name": model["name"],
        "capability_frameworks": list(model["frameworks"]),
        "capability_indicators": [deepcopy(item) for item in model["indicators"]],
    }


def model_indicators_by_id(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["indicator_id"]: item for item in model["indicators"]}
