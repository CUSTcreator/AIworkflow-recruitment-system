from __future__ import annotations

from typing import Any


SUMMARY_SCHEMA_VERSION = "candidate_decision_overview_v4"
SUMMARY_STAGES = {
    "screening",
    "after_first_interview",
    "after_second_interview",
}

SUMMARY_BODY_MAX_LENGTH = 100

# 单次展示调用的传输合同：模型读取后端已冻结的推荐程度、四项评分及其
# 可读解释材料，只输出不超过 100 字的摘要句和页面明细文案。业务对象 ID、
# 规则触发码和原始证据不进入模型。
# ``input_index`` 只是数组位置，不是业务对象标识，用于防止文案错绑。
#
# 推荐程度在请求中是后端策略结果，LLM 不得返回或修改；其余文案项仍按
# ``input_index`` 做明细级校验与降级，避免单条坏文案丢失整份已完成的评分结果。
PRESENTATION_BUNDLE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "strengths", "weaknesses", "verification_focus"],
    "properties": {
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["strength_sentence", "risk_sentence", "action_sentence"],
            "properties": {
                "strength_sentence": {"type": "string", "maxLength": 70},
                "risk_sentence": {"type": "string", "maxLength": 70},
                "action_sentence": {"type": "string", "maxLength": 70},
            },
        },
        "strengths": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["input_index", "title", "summary"],
                "properties": {
                    "input_index": {"type": "integer", "minimum": 1},
                    "title": {"type": "string", "minLength": 1, "maxLength": 40},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 120},
                },
            },
        },
        "weaknesses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["input_index", "title", "summary"],
                "properties": {
                    "input_index": {"type": "integer", "minimum": 1},
                    "title": {"type": "string", "minLength": 1, "maxLength": 40},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 120},
                },
            },
        },
        "verification_focus": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["input_index", "title", "reason", "verification_goal"],
                "properties": {
                    "input_index": {"type": "integer", "minimum": 1},
                    "title": {"type": "string", "minLength": 1, "maxLength": 50},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 120},
                    "verification_goal": {"type": "string", "minLength": 1, "maxLength": 160},
                },
            },
        },
    },
}
