"""一面题单的 LLM 提案生成。

LLM 只为既定 QuestionSlot 填充可读题干和 Rubric 文案；它不能新增、删除或重新绑定
InterviewTarget。模型失败时返回空提案，由规则层按同一槽位生成确定性回退题。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from recruitment_ai_core.llm import call_json_llm, load_llm_settings

from .contracts import FirstInterviewPlanningInput
from .policy import PLANNING_LLM_SCHEMA_VERSION, PLANNING_PROMPT_VERSION
from .result_contracts import (
    QuestionDraftProposal,
    QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult,
)


WORKFLOW_NAME = "first_interview_planning"


def try_generate_question_proposals(
    input_data: FirstInterviewPlanningInput,
    constraints: QuestionPlanningConstraintSet,
) -> QuestionProposalGenerationResult:
    """调用 LLM 生成槽位提案；异常时保留可观测信息并降级到规则回退。

    输入仅为冻结岗位名称和约束集中的目标/证据/槽位，输出只能是按 slot_id 对齐的
    QuestionDraftProposal[]。模型不响应、格式错误或调用失败时返回空提案和 llm_trace，
    由规则层对原槽位生成确定性回退题，不会改变任何业务关联。
    """
    # 1. 合并本次显式配置与题单固定超时/JSON 要求，避免调用方关闭结构化输出。
    overrides = _deep_merge(
        _llm_overrides(input_data),
        {"workflows": {WORKFLOW_NAME: {"timeout_seconds": 20, "json_mode": True, "strict_json_schema": False}}},
    )
    settings = load_llm_settings(overrides)
    trace: dict[str, Any] | None = None
    try:
        # 2. Prompt 传入全部冻结目标和全部槽位；不再在这里做 [:5] 等二次截断。
        response, trace = call_json_llm(
            workflow_name=WORKFLOW_NAME,
            messages=_messages(input_data, constraints),
            schema_name=PLANNING_LLM_SCHEMA_VERSION,
            settings_overrides=overrides,
        )
        if response is None:
            return QuestionProposalGenerationResult(
                generation_warnings=["first_interview_llm_no_response"], llm_trace=trace
            )
        proposals = _coerce_proposals(response, constraints)
        return QuestionProposalGenerationResult(
            proposals=proposals,
            generation_mode="llm",
            llm_trace=trace,
        )
    except Exception as exc:
        if getattr(exc, "retryable", False):
            raise
        if not settings.fail_open:
            raise
        failure_trace: dict[str, Any] = {
            "mode": "fallback_after_llm_error",
            "error": f"{type(exc).__name__}:{str(exc)[:300]}",
        }
        if isinstance(trace, dict):
            failure_trace["call"] = trace
        failed_call = getattr(exc, "llm_trace", None)
        if isinstance(failed_call, dict) and "call" not in failure_trace:
            failure_trace["call"] = failed_call
        return QuestionProposalGenerationResult(
            generation_warnings=[f"first_interview_llm_fallback:{type(exc).__name__}"],
            llm_trace=failure_trace,
        )


def _messages(
    input_data: FirstInterviewPlanningInput,
    constraints: QuestionPlanningConstraintSet,
) -> list[dict[str, str]]:
    """构造最小化 Prompt：只暴露 LLM 填写题干所需字段，业务 ID 和题型只读。"""
    system_prompt = f"""
你是一面技术题单设计器，不是最终评分器。

你只能为输入中的 question_slots 逐槽位生成题目提案：不得新增、删除或重排 InterviewTarget；
不得修改槽位中的 question_type、purpose、expected_result_type、目标能力 ID 或证据 ID。
每个 experience_fact 题只核验历史事实；每个 interview_performance 题应提供对应评分目标的
Rubric 文案。输出必须是 JSON object，schema_version 必须为 {PLANNING_LLM_SCHEMA_VERSION}。
""".strip()
    user_payload = {
        "prompt_version": PLANNING_PROMPT_VERSION,
        "application_id": input_data.application_id,
        "job_title": input_data.job_title,
        "open_interview_targets": constraints.target_snapshots,
        "question_slots": constraints.question_slots,
        "required_output_keys": ["schema_version", "question_proposals"],
        "question_proposal_schema": {
            "slot_id": "必须来自 question_slots",
            "question_text": "面试官可直接使用的题干",
            "follow_up_questions": ["最多 3 条追问"],
            "expected_evidence": ["希望候选人提供的事实或现场表现"],
            "evaluation_rubrics": ["仅 requires_rubric=true 的槽位填写"],
        },
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _coerce_proposals(
    payload: Mapping[str, Any], constraints: QuestionPlanningConstraintSet
) -> list[QuestionDraftProposal]:
    """校验 LLM 输出的 schema 和 slot_id，只保留一槽一题且题干非空的提案。"""
    if str(payload.get("schema_version") or "") != PLANNING_LLM_SCHEMA_VERSION:
        raise ValueError("invalid_first_interview_planning_llm_schema_version")
    allowed_slots = {str(item.get("slot_id") or "") for item in constraints.question_slots}
    output: list[QuestionDraftProposal] = []
    used: set[str] = set()
    raw_items = payload.get("question_proposals")
    if not isinstance(raw_items, list):
        raise ValueError("first_interview_question_proposals_missing")
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        slot_id = str(item.get("slot_id") or "")
        question_text = str(item.get("question_text") or item.get("question") or "").strip()
        if not slot_id or slot_id not in allowed_slots or slot_id in used or not question_text:
            continue
        used.add(slot_id)
        output.append(QuestionDraftProposal(
            slot_id=slot_id,
            question_text=question_text,
            follow_up_questions=_strings(item.get("follow_up_questions")),
            expected_evidence=_strings(item.get("expected_evidence")),
            evaluation_rubrics=[dict(value) for value in item.get("evaluation_rubrics", []) if isinstance(value, Mapping)],
        ))
    return output


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:3]


def _llm_overrides(input_data: FirstInterviewPlanningInput) -> dict[str, Any]:
    value = input_data.metadata.get("llm_config") if isinstance(input_data.metadata, dict) else None
    return value if isinstance(value, dict) else {}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result