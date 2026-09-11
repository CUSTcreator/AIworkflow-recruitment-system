"""题单与面评执行态实体的统一运行时投影。

这些函数只在 ORM 具名列与算法/DTO 所需普通字典之间转换；不读取、不回写 payload。
"""
from __future__ import annotations

from typing import Any

from backend.app.models.entities import InterviewParseResultRecord, InterviewRecordRecord, InterviewTarget


def interview_target_view(row: InterviewTarget) -> dict[str, Any]:
    """将具名 Target 字段投影为题单与 V2/V3 共用的严格合同。"""
    return {
        "interview_target_id": row.interview_target_id,
        "purpose": row.purpose,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "title": row.title,
        "verification_goal": row.verification_goal,
        "trigger_code": row.trigger_code,
        "status": row.status,
        "stage_created": row.stage_created,
        "resolved_stage": row.resolved_stage,
        "resolution_note": row.resolution_note,
        "source_result_ids": list(row.source_result_ids or []),
        "evidence_ids": list(row.evidence_ids or []),
        "attributes": dict(row.attributes or {}),
    }


def interview_record_view(row: InterviewRecordRecord) -> dict[str, Any]:
    """投影不可变原始面评记录，避免向 V2/V3 传入 ORM 或泛化 JSON。"""
    return {
        "record_id": row.record_id,
        "interview_id": row.interview_id,
        "stage": row.stage,
        "record_type": row.record_type,
        "question_id": row.question_id,
        "raw_text": row.raw_text,
        "answer_status": row.answer_status,
        "segments": list(row.segments_json or []),
        "source_input": dict(row.source_input_json or {}),
    }


def interview_parse_result_view(row: InterviewParseResultRecord) -> dict[str, Any]:
    """投影已发布 IPR，供 V3 增量证据重放；分数仍只来自 AAV。"""
    return {
        "parse_result_id": row.parse_result_id,
        "stage": row.stage,
        "version": row.version,
        "parse_schema_version": row.parse_schema_version,
        "source_record_ids": list(row.source_record_ids or []),
        "segments": list(row.segments_json or []),
        "assertions": list(row.assertions_json or []),
        "parser_version": row.parser_version,
        "source_hash": row.source_hash,
        "no_new_evidence": row.no_new_evidence,
        "resolved_interview_target_ids": list(row.resolved_interview_target_ids or []),
    }
