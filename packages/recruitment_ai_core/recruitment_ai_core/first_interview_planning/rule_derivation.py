"""一面题单的纯规则派生与校验。

本模块不调用 LLM、不访问数据库。它把 LLM 的槽位提案收敛为可执行题单，负责 Target 边界、稳定 ID、
Rubric 完整性、覆盖率和确定性回退题。
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

# 规则模块只能依赖同级纯合同，不能反向导入包根；否则包根导出该模块时会循环导入。
from .contracts import FirstInterviewPlanningInput
from .result_contracts import (
    QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult,
    QuestionRuleDerivationResult,
)


class FirstInterviewRuleDerivationService:
    """将题目提案校验为可确认、可执行、可回放的正式草稿。

    输入是冻结约束与 LLM 文案提案；输出是规则事实，不是前端 DTO。稳定 ID、Target/证据绑定、
    题型、场景和 Rubric 只能在本服务补齐或校验，不能由 LLM 或页面随意推导。
    """

    def derive(
        self,
        *,
        planning_input: FirstInterviewPlanningInput,
        constraints: QuestionPlanningConstraintSet,
        proposal_result: QuestionProposalGenerationResult,
    ) -> QuestionRuleDerivationResult:
        """从约束和提案生成唯一可执行题目列表及覆盖率。

        输入：规划配置、QuestionPlanningConstraintSet、QuestionDraftProposal[]；输出：每道题的稳定
        业务字段、推荐集合、覆盖结果和告警。此函数无 ORM 与 LLM 依赖，可独立重复执行。
        """
        # 1. 每个槽位至多接收一份 LLM 提案；缺失或不合法时仍保留槽位并走确定性回退。
        proposals = {item.slot_id: item for item in proposal_result.proposals}
        targets = {
            str(item.get("interview_target_id") or ""): item
            for item in constraints.target_snapshots
        }
        warnings = [*constraints.generation_warnings, *proposal_result.generation_warnings]
        questions: list[dict[str, Any]] = []
        for slot in constraints.question_slots:
            target_ids = [str(item) for item in slot.get("interview_target_ids", []) if str(item) in targets]
            if not target_ids or len(target_ids) > planning_input.generation_config.max_targets_per_question:
                warnings.append(f"first_interview_slot_target_invalid:{slot.get('slot_id')}")
                continue
            proposal = proposals.get(str(slot.get("slot_id") or ""))
            question = self._validated_question(slot, targets, proposal)
            questions.append(question)

        # 2. 用“槽位绑定关系”计算覆盖率，而不是相信 LLM 自报的覆盖结论。
        target_order = [str(item.get("interview_target_id") or "") for item in constraints.target_snapshots]
        covered_ids = {
            target_id
            for question in questions
            for target_id in question["interview_target_ids"]
        }
        coverage = {
            "targetCount": len(target_order),
            "coveredTargetIds": [item for item in target_order if item in covered_ids],
            "uncoveredTargetIds": [item for item in target_order if item not in covered_ids],
            "questionCount": len(questions),
            "recommendedQuestionCount": sum(1 for item in questions if item["recommended"]),
        }
        if coverage["uncoveredTargetIds"]:
            warnings.append("first_interview_target_coverage_incomplete")
        if len(questions) > planning_input.generation_config.max_question_suggestions:
            raise RuntimeError("first_interview_question_suggestion_limit_exceeded")
        return QuestionRuleDerivationResult(
            validated_questions=questions,
            recommended_question_ids=[item["question_id"] for item in questions if item["recommended"]],
            coverage=coverage,
            generation_warnings=list(dict.fromkeys(warnings)),
        )

    def _validated_question(
        self,
        slot: Mapping[str, Any],
        targets: Mapping[str, Mapping[str, Any]],
        proposal: Any | None,
    ) -> dict[str, Any]:
        slot_id = str(slot["slot_id"])
        target_ids = [str(item) for item in slot.get("interview_target_ids", [])]
        primary_target = targets[target_ids[0]]
        probe_angle = str(slot["probe_angle"])
        question_id = _stable_id("QF", slot_id)
        question_text = str(getattr(proposal, "question_text", "") or "").strip() or _fallback_question(primary_target, probe_angle)
        follow_ups = list(getattr(proposal, "follow_up_questions", []) or []) or _fallback_follow_ups(probe_angle)
        expected = list(getattr(proposal, "expected_evidence", []) or []) or _fallback_expected_evidence(probe_angle)
        requires_rubric = bool(slot.get("requires_rubric"))
        rubrics = _validated_rubrics(
            question_id=question_id,
            base_rubrics=list(slot.get("base_rubrics") or []),
            proposed=list(getattr(proposal, "evaluation_rubrics", []) or []),
            required=requires_rubric,
        )
        if requires_rubric and not rubrics:
            raise RuntimeError(f"first_interview_rubric_required:{slot_id}")
        return {
            # 1. 稳定业务标识由程序生成，避免 LLM 输出不可回放的随机 ID。
            "question_id": question_id,
            "slot_id": slot_id,
            "question_type": str(slot["question_type"]),
            "probe_angle": probe_angle,
            "purpose": str(slot["purpose"]),
            "expected_result_type": str(slot["expected_result_type"]),
            "scenario_id": _stable_id("SCN", slot_id) if str(slot["question_type"]) == "direct_task" else None,
            # 2. 目标、能力叶子与证据均复制自约束集；LLM 不具备修改这些关联的权限。
            "interview_target_ids": target_ids,
            "target_job_capability_ids": list(slot.get("target_job_capability_ids") or []),
            "target_preset_indicator_ids": list(slot.get("target_preset_indicator_ids") or []),
            "source_evidence_ids": list(slot.get("source_evidence_ids") or []),
            "priority": str(slot["priority"]),
            "recommended": probe_angle in {"fact_reconstruction", "direct_task"} or str(slot["priority"]) == "high",
            # 3. 以下是可读内容；LLM 缺失时使用确定性回退，不改变上面的业务约束。
            "question_text": question_text,
            "follow_up_questions": follow_ups[:3],
            "expected_evidence": expected[:5],
            "negative_signals": _negative_signals(probe_angle),
            "evaluation_rubrics": rubrics,
        }


def _validated_rubrics(
    *,
    question_id: str,
    base_rubrics: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
    required: bool,
) -> list[dict[str, Any]]:
    if not required:
        return []
    output: list[dict[str, Any]] = []
    for base in base_rubrics:
        target_type, target_id = str(base.get("target_type") or ""), str(base.get("target_id") or "")
        proposal = next(
            (
                item for item in proposed
                if str(item.get("target_type") or "") == target_type
                and str(item.get("target_id") or "") == target_id
                and _valid_anchor_list(item.get("level_anchors"), int(base.get("max_level") or 0))
            ),
            None,
        )
        anchors = proposal.get("level_anchors") if isinstance(proposal, Mapping) else base.get("level_anchors")
        output.append({
            "rubric_id": _stable_id("RB", question_id, target_type, target_id),
            "target_type": target_type,
            "target_id": target_id,
            "max_level": int(base.get("max_level") or 4),
            "level_anchors": [dict(item) for item in anchors or []],
        })
    return output


def _valid_anchor_list(value: Any, max_level: int) -> bool:
    if not isinstance(value, list) or len(value) != max_level:
        return False
    levels = [item.get("level") for item in value if isinstance(item, Mapping)]
    return levels == list(range(1, max_level + 1)) and all(
        isinstance(item, Mapping) and str(item.get("description") or "").strip() for item in value
    )


def _fallback_question(target: Mapping[str, Any], probe_angle: str) -> str:
    title = str(target.get("title") or target.get("verification_goal") or "该能力")
    if probe_angle == "contribution_boundary":
        return f"围绕“{title}”，请具体说明哪些设计、代码和关键决策由你亲自负责，哪些由团队其他成员完成？"
    if probe_angle == "evidence_crosscheck":
        return f"围绕“{title}”，你如何验证方案或结果有效？请给出指标、测试、日志、上线结果或复盘中的具体证据。"
    if probe_angle == "capability_probe":
        return f"针对“{title}”，请说明你的技术判断、方案取舍、异常处理和验证方式。"
    if probe_angle == "direct_task":
        return f"请现场设计一个与“{title}”相关的实现方案，说明任务拆分、关键约束、失败处理和验收方法。"
    return f"请结合一个真实项目说明“{title}”：当时的背景、你的个人动作、关键细节和最终结果分别是什么？"


def _fallback_follow_ups(probe_angle: str) -> list[str]:
    mapping = {
        "contribution_boundary": ["你独立做出的关键决策是什么？", "团队协作边界如何划分？"],
        "evidence_crosscheck": ["证据如何采集？", "出现异常时你如何定位和修正？"],
        "capability_probe": ["为什么选择该方案？", "有哪些替代方案和取舍？"],
        "direct_task": ["输入输出如何定义？", "失败重试和效果验收如何设计？"],
    }
    return mapping.get(probe_angle, ["你在其中具体负责什么？", "结果如何被验证？"])


def _fallback_expected_evidence(probe_angle: str) -> list[str]:
    mapping = {
        "contribution_boundary": ["个人负责范围", "独立决策点", "团队协作边界"],
        "evidence_crosscheck": ["测试或指标", "日志或演示", "失败处理事实"],
        "capability_probe": ["技术判断", "方案取舍", "验证路径"],
        "direct_task": ["任务拆分", "工程约束", "验收方案"],
    }
    return mapping.get(probe_angle, ["项目背景", "个人动作", "可验证结果"])


def _negative_signals(probe_angle: str) -> list[str]:
    mapping = {
        "contribution_boundary": ["只描述团队成果", "无法区分个人贡献"],
        "evidence_crosscheck": ["无法提供可核查证据", "回避失败或异常路径"],
        "capability_probe": ["只报技术名词", "无法说明取舍和边界"],
        "direct_task": ["无法拆解任务", "未考虑失败处理与验收"],
    }
    return mapping.get(probe_angle, ["只讲项目背景", "缺少个人动作与结果"])


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"