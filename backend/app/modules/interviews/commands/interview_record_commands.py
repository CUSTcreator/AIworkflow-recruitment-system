"""Interview 面评记录命令：写入原始面试记录和任务状态。"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    Interview,
    InterviewRecordRecord,
    Task,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _segments(
    record_id: str,
    record_type: str,
    raw_text: str | None,
    source_payload: dict[str, Any],
) -> list[dict[str, str]]:
    if record_type == "free_note":
        return [{"segment_id": f"{record_id}_SEG_1", "speaker": "interviewer", "text": raw_text}] if raw_text else []
    candidate_text = str(
        source_payload.get("rawText")
        or source_payload.get("answerText")
        or source_payload.get("answerSummary")
        or ""
    ).strip()
    interviewer_note = str(source_payload.get("interviewerNote") or "").strip()
    result: list[dict[str, str]] = []
    if candidate_text:
        result.append({"segment_id": f"{record_id}_SEG_1", "speaker": "candidate", "text": candidate_text})
    if interviewer_note:
        result.append(
            {
                "segment_id": f"{record_id}_SEG_{len(result) + 1}",
                "speaker": "interviewer",
                "text": interviewer_note,
            }
        )
    return result


class InterviewRecordCommands:
    def __init__(self, db: Session) -> None:
        self.db = db

    def persist(
        self,
        app: Application,
        *,
        interview_id: str,
        stage: str,
        body: dict[str, Any],
    ) -> list[str]:
        # ``raw_text=None`` 是合法业务值：题目未问或面试官未录入回答。它不是解析失败，
        # 后续 V2/V3 会将其解释为“本轮无新增面试证据”。
        records: list[tuple[str, str | None, str | None, dict[str, Any]]] = []
        raw_notes = str(body.get("rawNotes") or "").strip()
        if raw_notes:
            records.append(("free_note", None, raw_notes, {"source": "rawNotes"}))
        for index, response in enumerate(body.get("questionResponses") or []):
            if not isinstance(response, dict):
                continue
            question_id = str(
                response.get("questionId")
                or response.get("question_id")
                or f"question_{index + 1}"
            )
            candidate_text = str(
                response.get("rawText")
                or response.get("answerText")
                or response.get("answerSummary")
                or ""
            ).strip()
            interviewer_note = str(response.get("interviewerNote") or "").strip()
            raw_text = "\n".join(text for text in (candidate_text, interviewer_note) if text) or None
            records.append(("question_answer", question_id, raw_text, response))

        record_ids: list[str] = []
        for record_type, question_id, raw_text, source_payload in records:
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "application_id": app.application_id,
                        "stage": stage,
                        "record_type": record_type,
                        "question_id": question_id,
                        "raw_text": raw_text,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:24]
            record_id = f"IR_{digest.upper()}"
            record_ids.append(record_id)
            if record_type == "free_note":
                body["_rawNotesRecordId"] = record_id
            else:
                source_payload["sourceInterviewRecordId"] = record_id
            existing = self.db.get(InterviewRecordRecord, record_id) or next(
                (
                    item
                    for item in self.db.new
                    if isinstance(item, InterviewRecordRecord) and item.record_id == record_id
                ),
                None,
            )
            if existing is not None:
                continue
            self.db.add(
                InterviewRecordRecord(
                    record_id=record_id,
                    application_id=app.application_id,
                    interview_id=interview_id,
                    stage=stage,
                    record_type=record_type,
                    question_id=question_id,
                    raw_text=raw_text,
                    answer_status=(
                        source_payload.get("answerStatus")
                        or source_payload.get("answer_status")
                        or ("answered" if raw_text else "not_recorded")
                    ) if record_type == "question_answer" else None,
                    segments_json=_segments(record_id, record_type, raw_text, source_payload),
                    source_input_json=source_payload,
                )
            )
        round_row = self.db.get(Interview, interview_id) or next(
            (item for item in self.db.new if isinstance(item, Interview) and item.interview_id == interview_id),
            None,
        )
        if round_row is None:
            round_row = Interview(
                interview_id=interview_id,
                application_id=app.application_id,
                interview_type="technical_first_round" if stage == "interview_1" else "hr_second_round",
                status="recording",
            )
            self.db.add(round_row)
        body["_sourceInterviewRecordIds"] = record_ids
        return record_ids

    def mark_task_processing(self, application_id: str, task_type: str, title: str) -> None:
        tasks = self.db.scalars(
            select(Task).where(
                Task.application_id == application_id,
                Task.task_type == task_type,
                Task.status != "done",
            )
        ).all()
        for task in tasks:
            task.status = "in_progress"
            task.title = title
            task.updated_at = _now()

    def mark_task_failed(self, application_id: str, interview_round: str) -> None:
        first = interview_round == "first"
        task_type = "conduct_first_interview" if first else "conduct_second_interview"
        title = "一面后评估更新失败，请重新提交" if first else "二面后评估更新失败，请重新提交"
        tasks = self.db.scalars(
            select(Task).where(
                Task.application_id == application_id,
                Task.task_type == task_type,
                Task.status != "done",
            )
        ).all()
        for task in tasks:
            task.status = "pending"
            task.title = title
            task.updated_at = _now()
