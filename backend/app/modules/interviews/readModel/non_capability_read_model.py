"""面试非能力信息读模型：组装薪资、到岗等不计分的招聘参考信息。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import InterviewParseResultRecord


VISIBLE_REASON_CODES = {
    "compensation",
    "availability",
    "location",
    "internship_duration",
    "work_schedule",
    "motivation",
    "career_intention",
    "subjective_impression",
    "administrative",
    "other",
}

LABELS = {
    "compensation": "期望薪资",
    "availability": "到岗时间",
    "location": "工作地点",
    "internship_duration": "实习周期",
    "work_schedule": "出勤安排",
    "motivation": "求职动机",
    "career_intention": "职业意向",
    "subjective_impression": "面试印象",
    "administrative": "流程信息",
    "other": "其他信息",
}

def build_non_capability_card(
    db: Session, application_id: str, *, stage: str | None = None
) -> dict[str, Any]:
    query = select(InterviewParseResultRecord).where(
        InterviewParseResultRecord.application_id == application_id
    )
    # 工作台传入阶段时只显示本轮；旧二面复盘页不传，保持原有跨轮聚合行为。
    if stage is not None:
        query = query.where(InterviewParseResultRecord.stage == stage)
    rows = db.scalars(query.order_by(
        InterviewParseResultRecord.version.desc(),
        InterviewParseResultRecord.created_at.desc(),
    )).all()
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        for item in (row.assertions_json or []):
            if not isinstance(item, dict) or item.get("disposition") != "non_scoring":
                continue
            # 解析合同允许业务分类使用驼峰或下划线；缺失分类时才归入“其他信息”。
            reason = str(item.get("reasonCode") or item.get("reason_code") or "other").strip() or "other"
            if reason not in VISIBLE_REASON_CODES:
                reason = "other"
            text = str(item.get("text") or "").strip() or "；".join(
                str(ref.get("quote") or "").strip()
                for ref in item.get("sourceRefs", [])
                if str(ref.get("quote") or "").strip()
            )
            if not text:
                continue
            key = (reason, text)
            if key in seen:
                continue
            seen.add(key)
            status = {
                "positive": "normal",
                "negative": "attention",
                "neutral": "note",
            }.get(str(item.get("polarity") or "neutral"), "note")
            items.append(
                {
                    "itemId": item.get("assertionId")
                    or f"{row.parse_result_id}:{len(items) + 1}",
                    "label": LABELS.get(reason, "其他信息"),
                    "value": text,
                    "reasonCode": reason,
                    "status": status,
                    "sourceStage": row.stage,
                }
            )
    return {
        "title": "非能力信息",
        "description": "用于招聘决策参考，不计入候选人能力分。",
        "items": items,
    }
