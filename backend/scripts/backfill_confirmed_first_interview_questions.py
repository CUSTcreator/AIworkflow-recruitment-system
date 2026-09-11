"""补齐历史上已确认但未物化逐题记录的一面题单。

仅处理：PlanVersion 已 confirmed、存在对应 Guide、Guide 下没有 InterviewQuestion、且规划展示层
仍保留 question_suggestions 的记录。该脚本不重新生成题目、不修改 Application 主状态；只把当时
面试官已确认的建议题补写为正式执行记录，使 V2 能按既定 PlanVersion 精确冻结来源。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.db.session import SessionLocal
from backend.app.models.entities import (
    FirstInterviewPlanVersion,
    InterviewGuide,
    InterviewQuestion,
)


def _suggestions(plan: FirstInterviewPlanVersion) -> list[dict]:
    presentation = dict(plan.presentation_json or {})
    candidates = [
        presentation.get("question_suggestions"),
        dict(presentation.get("draft_guide") or {}).get("questionSuggestions"),
    ]
    for values in candidates:
        if isinstance(values, list):
            rows = [dict(item) for item in values if isinstance(item, dict)]
            if rows:
                return rows
    return []


def main() -> None:
    repaired: list[tuple[str, str, int]] = []
    with SessionLocal() as db:
        plans = db.scalars(
            select(FirstInterviewPlanVersion).where(
                FirstInterviewPlanVersion.status == "confirmed"
            )
        ).all()
        for plan in plans:
            guide = db.scalars(
                select(InterviewGuide)
                .where(
                    InterviewGuide.application_id == plan.application_id,
                    InterviewGuide.plan_version_id == plan.plan_version_id,
                )
                .order_by(InterviewGuide.created_at.desc(), InterviewGuide.id.desc())
            ).first()
            if guide is None or not guide.guide_id:
                continue
            existing = db.scalars(
                select(InterviewQuestion).where(
                    InterviewQuestion.application_id == plan.application_id,
                    InterviewQuestion.plan_version_id == plan.plan_version_id,
                    InterviewQuestion.guide_id == guide.guide_id,
                )
            ).all()
            if existing:
                continue
            suggestions = _suggestions(plan)
            if not suggestions:
                continue
            questions = [
                {
                    **question,
                    "confirmed": True,
                    "sectionType": "technical",
                    "guideId": guide.guide_id,
                    "planVersionId": plan.plan_version_id,
                }
                for question in suggestions
            ]
            guide_payload = dict(guide.payload or {})
            guide_payload["questions"] = questions
            guide_payload["technicalQuestions"] = questions
            guide_payload["questionCount"] = len(questions)
            guide_payload["questionMaterializedAt"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
            guide_payload["questionMaterializationReason"] = "backfill_confirmed_suggestions"
            guide.payload = guide_payload
            for question in questions:
                db.add(
                    InterviewQuestion(
                        application_id=plan.application_id,
                        guide_id=guide.guide_id,
                        plan_version_id=plan.plan_version_id,
                        payload=question,
                    )
                )
            repaired.append((plan.application_id, plan.plan_version_id, len(questions)))
        db.commit()
    for application_id, plan_version_id, count in repaired:
        print(f"repaired application={application_id} plan={plan_version_id} questions={count}")
    print(f"repaired_count={len(repaired)}")


if __name__ == "__main__":
    main()
