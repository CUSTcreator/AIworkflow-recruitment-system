from __future__ import annotations

import json
from typing import Any

from recruitment_ai_core.llm import call_json_llm

from .contracts import PRESENTATION_BUNDLE_JSON_SCHEMA, SUMMARY_SCHEMA_VERSION


WORKFLOW_BY_STAGE = {
    "screening": "screening_scoring",
    "after_first_interview": "post_first_scoring",
    "after_second_interview": "post_second_scoring",
}


def try_generate_presentation_bundle(bundle_input: dict[str, Any], *, stage: str, settings_overrides: dict[str, Any] | None, validation_error: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """单次生成摘要句、优势、薄弱项与核验重点；推荐等级由后端决定。"""
    # 展示重试不需要把内部异常名称暴露给模型；请求体只保留业务事实，
    # 避免技术错误被模型复述为候选人的页面结论。
    del validation_error
    prompt = """
你是招聘评估展示助手。输入包含后端已经确定的推荐结论、四项冻结评分、优势、薄弱项和后续面试关注点；只能基于这些输入生成页面文案。

推荐等级和阶段显示文字已经由后端确定。不得输出、修改或质疑推荐等级，也不得把推荐等级写入摘要句。只能生成 2～3 句摘要正文：第一句概括最重要的优势，第二句概括最重要的风险，第三句说明下一轮需要确认的事实；没有对应事实时返回空字符串。

所有评分范围均为 0 到 100。不要自行重新计算分数，不要把教育分较高误写成岗位能力充分，不要把核验重点写成候选人已经具备的能力。不得输出“综合表现良好”“优缺点并存”“建议进一步了解”等没有具体事实的套话。

随后逐条输出 strengths、weaknesses、verification_focus。每个正常输入项必须且只能对应一个输出项，不得合并、删除或新增；每项必须复制对应的 input_index（仅表示数组位置，不是业务 ID），数组顺序可以变化。
输出字段仅限 summary.strength_sentence、summary.risk_sentence、summary.action_sentence，以及三类列表中各自的 input_index 和文案字段：strengths 使用 title、summary；weaknesses 使用 title、summary；verification_focus 使用 title、reason、verification_goal。不要输出内部 ID、原始证据、推荐等级、额外评分字段或其他字段。

后端会按 input_index 逐条校验文案，过滤重复、越界或非法条目，并使用冻结输入回填缺失项；缺失或非法文案不会阻断评分发布。不要因为后端会降级而省略正常输入项，也不要把技术错误写进页面文案。
""".strip()
    return _call(stage=stage, body=dict(bundle_input), prompt=prompt, schema=PRESENTATION_BUNDLE_JSON_SCHEMA, schema_name=f"{SUMMARY_SCHEMA_VERSION}_presentation_bundle", settings_overrides=settings_overrides)


def _call(*, stage: str, body: dict[str, Any], prompt: str, schema: dict[str, Any], schema_name: str, settings_overrides: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    workflow_name = WORKFLOW_BY_STAGE.get(stage, "screening_scoring")
    # 当前 OpenAI-compatible 供应商不支持 json_schema response_format；使用
    # json_object 传输模式，完整字段约束仍随 Prompt 发送，并由本地字段级
    # 校验器清洗/补齐，避免单条坏文案触发整份展示降级。
    overrides = _deep_merge(settings_overrides or {}, {"workflows": {workflow_name: {"timeout_seconds": 20, "json_mode": True, "strict_json_schema": False}}})
    system_prompt = prompt + "\n\n必须严格遵守以下JSON Schema：\n" + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return call_json_llm(
        workflow_name=workflow_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
        ],
        schema_name=schema_name,
        settings_overrides=overrides,
        json_schema=schema,
        # 只做传输层的最小形状检查；具体字段由下面的业务校验器逐项处理。
        local_validation_schema={"type": "object"},
    )


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        result[key] = _deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result
