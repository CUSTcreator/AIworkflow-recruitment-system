"""一面题单展示 DTO 构建。

题干已经在 LLM 提案阶段产生；本服务只把规则校验后的字段映射为前端契约，不能修改业务结论。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from recruitment_ai_core.first_interview_planning import (
    FirstInterviewPlanPresentationResult,
    QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult,
    QuestionRuleDerivationResult,
)
from recruitment_ai_core.first_interview_planning.contracts import FirstInterviewPlanningInput


class FirstInterviewPlanPresentationService:
    """构建题单确认页直接使用的 ``draft_guide`` 和 ``question_suggestions``。

    前后端字段对齐约定必须在这里维护：
    1. ``targets[]`` 的 ``targetId/title/unknownPoint/sourceEvidenceIds/priority`` 来自已冻结 Target；
    2. ``questions[]`` 的 ``questionId/mainQuestion/followUpQuestions/expectedEvidence/negativeSignals/``
       ``validationGoal/priority`` 是确认页直接展示字段；
    3. ``slotId/probeAngle/questionType/purpose/expectedResultType/scenarioId``、目标/证据/能力 ID 与
       ``evaluationRubrics`` 是一面后解析和 V2 评分必须透传的冻结字段，前端不可在保存时丢失。

    字段名转换只允许在本服务发生：规则层使用 snake_case，前端 DTO 使用 camelCase。
    """

    def build(
        self,
        *,
        planning_input: FirstInterviewPlanningInput,
        constraints: QuestionPlanningConstraintSet,
        proposal_result: QuestionProposalGenerationResult,
        rule: QuestionRuleDerivationResult,
    ) -> FirstInterviewPlanPresentationResult:
        target_views = [_front_target(item) for item in constraints.target_snapshots]
        target_goals = {
            str(item.get("interview_target_id") or ""): str(item.get("verification_goal") or "")
            for item in constraints.target_snapshots
        }
        suggestions = [_front_question(item, target_goals) for item in rule.validated_questions]
        recommended_ids = set(rule.recommended_question_ids)
        recommended_count = sum(
            1 for item in suggestions if item["questionId"] in recommended_ids
        )
        warning_list = list(dict.fromkeys([*rule.generation_warnings]))
        guide = {
            "guideVersion": "first_interview_plan_v3",
            "planId": f"PLAN_{planning_input.application_id}_FIRST",
            "applicationId": planning_input.application_id,
            "round": "first",
            "guideType": "technical_first_round",
            "title": "技术一面题单草稿",
            "summary": _summary(len(suggestions), recommended_count, len(target_views)),
            "goal": "围绕当前待确认目标核验事实、能力表现与工程判断。",
            "durationMinutes": 45,
            # 建议题与正式草稿必须分开：面试官明确点击“加入题单”后，题目才进入 questions。
            "questionCount": 0,
            "targets": target_views,
            "questions": [],
            "technicalQuestions": [],
            "questionSuggestions": suggestions,
            "recommendedQuestionIds": list(rule.recommended_question_ids),
            "coverage": dict(rule.coverage),
            "generationNotes": ["AI 建议题可编辑、排序、删除或新增；自定义题默认不进入能力评分。"],
            "interviewerNotes": "优先完成每个待确认目标的核验，再根据回答使用追问。",
            "confirmed": False,
        }
        return FirstInterviewPlanPresentationResult(
            planning_summary=_summary(len(suggestions), recommended_count, len(target_views)),
            question_suggestions=suggestions,
            draft_guide=guide,
            generation_mode=proposal_result.generation_mode,
            generation_warnings=warning_list,
            generated_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
        )


def _front_target(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "targetId": str(target.get("interview_target_id") or ""),
        "round": "first",
        "requirementId": str(target.get("target_id") or ""),
        "title": str(target.get("title") or "待验证能力"),
        "unknownPoint": str(target.get("verification_goal") or ""),
        "sourceEvidenceIds": list(target.get("source_evidence_ids") or []),
        "priority": str(target.get("priority") or "medium"),
        "selected": True,
        # 以下字段前端暂不展示，但保存/确认时必须透传给后续面评与 V2。
        "purpose": str(target.get("purpose") or ""),
        "targetType": str(target.get("target_type") or ""),
        "triggerCode": str(target.get("trigger_code") or ""),
    }


def _front_question(question: Mapping[str, Any], target_goals: Mapping[str, str]) -> dict[str, Any]:
    return {
        "questionId": str(question["question_id"]),
        "verificationTargetIds": list(question.get("interview_target_ids") or []),
        "mainQuestion": str(question.get("question_text") or ""),
        "followUpQuestions": list(question.get("follow_up_questions") or []),
        "expectedEvidence": list(question.get("expected_evidence") or []),
        "negativeSignals": list(question.get("negative_signals") or []),
        "priority": str(question.get("priority") or "medium"),
        "recommendation": str(question.get("priority") or "medium"),
        "validationGoal": "；".join(target_goals.get(str(item), str(item)) for item in question.get("interview_target_ids") or []),
        "confirmed": False,
        "sectionType": "technical",
        "sourceType": "ai_suggestion",
        "sourceSuggestionId": str(question["question_id"]),
        "finalQuestionId": str(question["question_id"]),
        "finalText": str(question.get("question_text") or ""),
        "isEdited": False,
        # 题单内部字段：前端目前不必渲染，但在保存、确认与一面后解析中必须保持不丢失。
        "slotId": str(question.get("slot_id") or ""),
        "probeAngle": str(question.get("probe_angle") or ""),
        "questionType": str(question.get("question_type") or ""),
        "purpose": str(question.get("purpose") or ""),
        "expectedResultType": str(question.get("expected_result_type") or ""),
        "scenarioId": question.get("scenario_id"),
        "targetJobCapabilityIds": list(question.get("target_job_capability_ids") or []),
        "targetPresetIndicatorIds": list(question.get("target_preset_indicator_ids") or []),
        "sourceEvidenceIds": list(question.get("source_evidence_ids") or []),
        "evaluationRubrics": list(question.get("evaluation_rubrics") or []),
    }


def _summary(suggestion_count: int, selected_count: int, target_count: int) -> str:
    return (
        f"已围绕 {target_count} 个当前待确认目标生成 {suggestion_count} 道候选问题，"
        f"默认推荐 {selected_count} 道；每道题均保留目标、证据与评分约束。"
    )