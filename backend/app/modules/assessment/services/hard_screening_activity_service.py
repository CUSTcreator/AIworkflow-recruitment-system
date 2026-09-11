"""硬筛中可恢复的语义判断活动。

硬筛的确定性规则仍在算法包内同步完成；本服务把可能触发 LLM 的完整判断请求
封装成一个 Activity。它不读取 ORM、不发布 HardScreeningResult，只返回可 JSON
持久化的草稿，正式结果仍由硬筛 Workflow 的发布 Step 原子写入。
"""
from __future__ import annotations

from typing import Any

from backend.app.infrastructure.workflow_runtime import (
    ActivityBatchResult,
    ActivityDefinition,
    ActivityPolicy,
    ActivityResolution,
    ActivityRunner,
    is_safe_model_degradation_error,
)
from backend.app.shared.workflows import ActivityExhaustionPolicy, ActivityOutcomeKind, StepContext
from recruitment_ai_core.hard_screening import evaluate_hard_screening


class HardScreeningActivityService:
    """为一次冻结硬筛输入提供活动级幂等与恢复边界。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    def evaluate(self, context: StepContext, source: dict[str, Any]) -> ActivityBatchResult:
        """运行或复用当前申请的硬筛结论。

        ``source`` 来自已冻结 Artifact，因此输入哈希会随简历、规则或 LLM 配置变化
        而变化。活动处理器只调用算法包，不允许修改 Application 或领域状态。
        """
        rules = list(source.get("rules") or [])

        def fallback(_activity, error: Exception) -> ActivityResolution:
            # 逐规则重放确定性判断；只有语义规则或单条重放失败才转人工复核，
            # 避免一次语义模型故障把正常的学历/年限/关键词规则全部丢掉。
            results = []
            for index, rule in enumerate(rules, start=1):
                if not isinstance(rule, dict) or not bool(rule.get("enabled", True)):
                    continue
                operator = str(rule.get("operator") or "")
                if operator != "semantic_match":
                    try:
                        deterministic = evaluate_hard_screening(
                            application_id=str(source.get("application_id") or ""),
                            resume_text=str(source.get("resume_text") or ""),
                            rules=[rule],
                            llm_config={},
                            resume_profile=dict(source.get("resume_profile") or {}),
                            candidate_facts=dict(source.get("candidate_facts") or {}),
                        )
                        if deterministic.get("rule_results"):
                            results.extend(deterministic["rule_results"])
                            continue
                    except Exception:
                        pass
                results.append(_manual_review_rule(
                    rule,
                    index,
                    reason_code="model_temporarily_unavailable",
                    reason="硬筛模型暂时不可用，可以稍后重试或直接人工处理。",
                ))
            failed = [item for item in results if item["status"] == "failed"]
            review = [item for item in results if item["status"] == "manual_review"]
            status = "failed" if failed else "manual_review" if review else "passed"
            payload = {
                "application_id": str(source.get("application_id") or ""),
                "status": status,
                "rule_results": results,
                "summary": (
                    "未通过：" + "；".join(item["name"] for item in failed)
                    if failed
                    else "硬筛模型暂时不可用，请重试或人工处理。"
                    if review
                    else "全部已启用的硬性条件均通过。"
                ),
                "policy": {
                    "rule_count": len(results),
                    "failed_count": len(failed),
                    "manual_review_count": len(review),
                },
                "schema_version": "hard_screening_result_v1",
            }
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.DEGRADED,
                payload=payload,
                resolution_code="hard_screening_manual_review_fallback",
                quality_summary={"usable": True, "degraded": True, "manualReviewCount": len(results), "errorType": type(error).__name__},
            )

        activity = ActivityDefinition(
            activity_key="hard_screening_evaluation",
            input_data={
                "applicationId": str(source.get("application_id") or ""),
                "resumeText": str(source.get("resume_text") or ""),
                "rules": rules,
                "llmConfig": dict(source.get("llm_config") or {}),
                "resumeProfile": dict(source.get("resume_profile") or {}),
                "candidateFacts": dict(source.get("candidate_facts") or {}),
            },
            handler=lambda _activity: evaluate_hard_screening(
                application_id=str(source.get("application_id") or ""),
                resume_text=str(source.get("resume_text") or ""),
                rules=rules,
                llm_config=dict(source.get("llm_config") or {}),
                resume_profile=dict(source.get("resume_profile") or {}),
                candidate_facts=dict(source.get("candidate_facts") or {}),
            ),
            policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
            exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
            on_exhausted=fallback,
            can_degrade=is_safe_model_degradation_error,
        )
        return self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[activity],
        )


def _manual_review_rule(
    rule: dict[str, Any],
    index: int,
    *,
    reason_code: str = "semantic_inconclusive",
    reason: str = "硬筛判断无法得出可靠结论，需要人工复核。",
) -> dict[str, Any]:
    """构造单条硬筛人工复核结果，字段与算法包正式结果一致。"""
    return {
        "rule_id": str(rule.get("rule_id") or rule.get("ruleId") or f"rule_{index}"),
        "criterion_type": str(rule.get("criterion_type") or rule.get("criterionType") or ""),
        "name": str(rule.get("name") or f"硬筛条件 {index}"),
        "source_scope": str(rule.get("source_scope") or rule.get("sourceScope") or "full_resume"),
        "operator": str(rule.get("operator") or ""),
        "expected_value": rule.get("expected_value", rule.get("expectedValue")),
        "status": "manual_review",
        "reason": reason,
        "reason_code": reason_code,
        "source_quotes": [],
    }
