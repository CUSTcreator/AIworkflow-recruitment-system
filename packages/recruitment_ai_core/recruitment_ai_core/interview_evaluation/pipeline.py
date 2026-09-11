"""统一的 V2/V3 面评解析管线。

面评只经过一条路径：冻结记录 -> 一次语义提取 -> assertions -> 锚点评估。
解析结果不再按经历、技能、观察和非评分拆成平行列表；这些只是断言的属性，
后续评分步骤只消费 assertions。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any

from recruitment_ai_core.llm import LLMResponseError, call_json_llm

from .contracts import (
    InterviewEvidenceExtractionResult,
    InterviewEvidenceItem,
    InterviewParseDraft,
    InterviewParseInput,
    InterviewSegment,
    ParseValidationIssue,
    ParseValidationResult,
)

logger = logging.getLogger(__name__)

_DISPOSITIONS = {"scored", "non_scoring", "review_required"}
_EVIDENCE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["items"],
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                # 行级合法性由下方本地过滤完成；允许坏行与好行同时返回，避免
                # 网关对整个数组做 all-or-nothing 校验而丢失好行。
                "required": [],
                "additionalProperties": True,
                "properties": {
                    "record_id": {"type": "string", "minLength": 1},
                    "text": {"type": "string", "minLength": 1},
                    "is_capability_evaluation": {"type": "boolean"},
                    "is_experience_related": {"type": "boolean"},
                },
            },
        }
    },
}


def has_interview_evidence(records: Sequence[Mapping[str, Any]]) -> bool:
    """冻结记录是否至少含一段非空文本。"""
    return any(_record_text(row).strip() for row in _records(records).values())


def extract_and_bind_interview_evidence(
    input_data: InterviewParseInput,
) -> InterviewEvidenceExtractionResult:
    """一次 LLM 提取面评断言的原文单元。

    LLM 只判断能力评价和经历相关性，不生成目标、分数或业务对象。所有原文引用
    都在本地校验；无效单元被丢弃，模型不可用时保留原文为非评分断言。
    """
    records = _records(input_data.interview_records)
    record_ids = tuple(records)
    if not has_interview_evidence(input_data.interview_records):
        return InterviewEvidenceExtractionResult(
            parser_version="interview_evidence_v4",
            source_record_ids=record_ids,
            items=(),
            no_new_evidence=True,
        )

    response = _embedded(records, "semantic_evidence")
    if response is None:
        response = _call_extraction(input_data, records)
    raw_items = response.get("items") if isinstance(response, Mapping) else None
    if not isinstance(raw_items, list):
        raw_items = _fallback_items(records)

    items: list[InterviewEvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_items, start=1):
        row = _mapping(raw)
        if set(row) - {"record_id", "text", "is_capability_evaluation", "is_experience_related"}:
            logger.warning("interview_evidence_unit_invalid_fields record_id=%s", row.get("record_id"))
            continue
        if any(key not in row for key in ("record_id", "text", "is_capability_evaluation", "is_experience_related")):
            logger.warning("interview_evidence_unit_missing_fields record_id=%s", row.get("record_id"))
            continue
        record_id = str(row.get("record_id") or row.get("recordId") or "")
        record = records.get(record_id)
        text = str(row.get("text") or "").strip()
        source_text = _record_text(record) if record else ""
        source_quote = _resolve_source_quote(source_text, text)
        if not record or not text or not source_quote:
            logger.warning("interview_evidence_unit_discarded record_id=%s", record_id)
            continue
        key = (record_id, text)
        if key in seen:
            continue
        seen.add(key)
        # 原文引用由本地依据冻结记录生成，不属于 LLM 输出合同。
        # 下游只保存重新定位后的连续原文。模型即使调整了空格、标点或做了轻微
        # 改写，也不会因此丢掉整条有效评价，更不会把模型改写当作原文引用。
        refs = _normalise_refs(None, record_id, source_quote)
        capability = bool(
            row.get(
                "is_capability_evaluation",
                row.get("isCapabilityEvaluation", row.get("disposition") == "scored"),
            )
        )
        # 经历相关性是能力评价的追加路由条件，不是独立入口。即使模型返回了
        # false/true 这种矛盾组合，也不能让非能力内容进入经历锚点评估。
        experience = capability and bool(
            row.get("is_experience_related", row.get("isExperienceRelated", False))
        )
        items.append(
            InterviewEvidenceItem(
                evidence_id=f"IE_{record_id}_{index}",
                record_id=record_id,
                source_refs=tuple(refs),
                disposition="scored" if capability else "non_scoring",
                text=source_quote,
                is_capability_evaluation=capability,
                is_experience_related=experience,
            )
        )
    return InterviewEvidenceExtractionResult(
        parser_version="interview_evidence_v4",
        source_record_ids=record_ids,
        items=tuple(items),
        no_new_evidence=False,
    )


def normalize_interview_evidence(
    input_data: InterviewParseInput,
    extraction: InterviewEvidenceExtractionResult,
) -> InterviewParseDraft:
    """把提取单元转换为唯一 assertions 合同。"""
    records = _records(input_data.interview_records)
    segments: list[InterviewSegment] = []
    assertions: list[dict[str, Any]] = []
    review_required = False
    for index, item in enumerate(extraction.items, start=1):
        record = records.get(item.record_id)
        quote = _first_quote(item.source_refs)
        if record is None or not quote or quote not in _record_text(record):
            review_required = True
            continue
        segment_id = f"SEG_{item.record_id}_{index}"
        refs = _attach_segment_ref(item.source_refs, segment_id)
        disposition = "scored" if item.disposition == "scored" else "non_scoring"
        segments.append(
            InterviewSegment(
                segment_id=segment_id,
                record_id=item.record_id,
                raw_text=quote,
                category=disposition,
                source_refs=tuple(refs),
                resolution_status="resolved" if disposition == "scored" else "no_effect",
            )
        )
        assertion: dict[str, Any] = {
            "assertionId": item.evidence_id,
            "recordId": item.record_id,
            "recordType": str(record.get("record_type") or "free_note"),
            "questionId": record.get("question_id") or record.get("questionId"),
            "text": item.text or quote,
            "sourceQuote": quote,
            "sourceRefs": refs,
            "disposition": disposition,
            "isCapabilityEvaluation": bool(item.is_capability_evaluation),
            "isExperienceRelated": bool(item.is_experience_related),
        }
        # 锚点 ID 在下一步依据冻结拓扑产生；解析层不伪造业务 ID。
        assertions.append(assertion)

    return InterviewParseDraft(
        parser_version="interview_evidence_v4",
        source_record_ids=extraction.source_record_ids,
        segments=tuple(segments),
        assertions=tuple(assertions),
        source_hash=_hash(
            {
                "stage": input_data.stage,
                "items": [item.as_dict() for item in extraction.items],
            }
        ),
        review_required=review_required,
        no_new_evidence=extraction.no_new_evidence,
    )


def validate_interview_parse_draft(draft: InterviewParseDraft) -> ParseValidationResult:
    """校验断言的来源和最小合同，不做评分。"""
    segment_map = {item.segment_id: item for item in draft.segments}
    issues: list[ParseValidationIssue] = []
    for segment in draft.segments:
        if segment.category not in {"scored", "non_scoring"}:
            issues.append(ParseValidationIssue("segment_category_invalid", "断言类别无效。", segment.segment_id))
        if segment.record_id not in draft.source_record_ids or not _refs_valid(segment.source_refs, segment_map):
            issues.append(ParseValidationIssue("segment_source_invalid", "断言必须引用冻结记录原文。", segment.segment_id))
    for assertion in draft.assertions:
        row = _mapping(assertion)
        assertion_id = str(row.get("assertionId") or "")
        refs = row.get("sourceRefs")
        if not assertion_id or not row.get("recordId") or not row.get("sourceQuote"):
            issues.append(ParseValidationIssue("assertion_contract_invalid", "断言缺少必要字段。"))
            continue
        if not _refs_valid(refs, segment_map):
            issues.append(ParseValidationIssue("assertion_source_invalid", "断言原文引用无效。"))
        if str(row.get("disposition") or "") not in _DISPOSITIONS:
            issues.append(ParseValidationIssue("assertion_disposition_invalid", "断言处理状态无效。"))
    return ParseValidationResult(draft=draft, issues=tuple(issues), needs_repair=bool(issues))


def repair_interview_parse_draft(validation: ParseValidationResult) -> InterviewParseDraft:
    """保留合法断言；非法断言不进入评分并标记需要人工复核。"""
    if not validation.needs_repair:
        return validation.draft
    invalid_segments = {item.segment_id for item in validation.issues if item.segment_id}
    segments = tuple(
        InterviewSegment(
            segment_id=item.segment_id,
            record_id=item.record_id,
            raw_text=item.raw_text,
            category=item.category,
            source_refs=item.source_refs,
            resolution_status="needs_review" if item.segment_id in invalid_segments else item.resolution_status,
            review_reason="解析断言未通过校验" if item.segment_id in invalid_segments else item.review_reason,
        )
        for item in validation.draft.segments
    )
    assertions = tuple(
        item
        for item in validation.draft.assertions
        if str(_mapping(item).get("sourceRefs") or "")
        and not any(
            str(ref.get("segment_id") or "") in invalid_segments
            for ref in (_mapping(item).get("sourceRefs") or ())
            if isinstance(ref, Mapping)
        )
    )
    return InterviewParseDraft(
        parser_version=validation.draft.parser_version,
        source_record_ids=validation.draft.source_record_ids,
        segments=segments,
        assertions=assertions,
        source_hash=validation.draft.source_hash,
        review_required=True,
        no_new_evidence=validation.draft.no_new_evidence,
    )


def _call_extraction(
    input_data: InterviewParseInput,
    records: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    payload = {
        "records": [
            {"record_id": record_id, "text": _record_text(record).strip()}
            for record_id, record in records.items()
            if _record_text(record).strip()
        ]
    }
    for attempt in range(2):
        try:
            response, _trace = call_json_llm(
                workflow_name=(
                    "post_first_scoring"
                    if input_data.stage == "after_first_interview"
                    else "post_second_scoring"
                ),
                messages=[
                    {"role": "system", "content": "只返回符合 JSON Schema 的对象；不要输出 Markdown。"},
                    {
                        "role": "user",
                        "content": (
                            "逐项完整提取面评中所有具有独立判断意义的陈述，不得只挑少量内容。"
                            "知识技能、项目或工作经历深度、个人动作与结果、岗位匹配、沟通协作、"
                            "学习适应、优势和风险等正面或负面评价都要覆盖。"
                            "每个 item 只能表达一个评价对象、一个评价主题和一个评价方向；教育背景、"
                            "专业技能、项目经历、工作经历、论文成果、岗位匹配、沟通协作等不同主题"
                            "必须分别返回，不能因为位于同一句或同一段而合并。正向和负向评价也必须"
                            "拆开。允许在逗号、分号等自然边界截取较短的连续原文，但不能把不相邻文字拼接。"
                            "每个 item 的 text 必须逐字复制对应 record 文本中的一段连续原文，"
                            "不得改写、概括、拼接或补充。只判断是否属于能力评价、是否与经历相关；"
                            "is_capability_evaluation 仅在该陈述直接评价候选人的知识、技能、行为、"
                            "经历表现或岗位胜任情况时为 true；流程意见、录用建议和单纯待核实事项为 false。"
                            "只有在能力评价成立，并且陈述直接评价项目或工作中的问题理解、个人行动、"
                            "方案执行、结果产出或这些经历的有效深度时，is_experience_related 才为 true。"
                            "仅表示缺少某个特定岗位实习、岗位名称不匹配或某项岗位技能不足，不等于对"
                            "上述通用经历能力作出评价，is_experience_related 应为 false。"
                            "例如‘本硕均为985/211，项目经历丰富，已发表论文’至少应按教育背景、"
                            "项目经历和论文成果拆成三个 item，不能整体作为一条评价。"
                            "不得绑定目标、生成分数或其他业务对象。每个 item 只返回 record_id、text、"
                            "is_capability_evaluation、is_experience_related 四个字段。\n"
                            + json.dumps(payload, ensure_ascii=False)
                        ),
                    },
                ],
                schema_name="interview_evidence_extraction_v4",
                json_schema=_EVIDENCE_EXTRACTION_SCHEMA,
            )
            if isinstance(response, Mapping) and isinstance(response.get("items"), list):
                return response
            raise ValueError("interview_evidence_items_invalid")
        except (LLMResponseError, ValueError, TypeError) as exc:
            logger.warning("interview_evidence_extract_retry attempt=%s error=%s", attempt + 1, str(exc)[:240])
    return {"items": _fallback_items(records), "degraded": True}


def _fallback_items(records: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "record_id": record_id,
            "text": text,
            "is_capability_evaluation": False,
            "is_experience_related": False,
        }
        for record_id, record in records.items()
        if (text := _record_text(record).strip())
    ]


def _resolve_source_quote(source_text: str, proposed_text: str) -> str:
    """把模型片段宽容地重新定位到连续原文，定位失败只丢当前片段。

    精确匹配优先；随后忽略全半角、大小写、空白和标点差异；最后只允许和某个
    原文分句高度相似的轻微改写回落到该分句。返回值始终来自原文，避免宽容校验
    演变成接受模型编造内容。
    """
    source = str(source_text or "")
    proposed = str(proposed_text or "").strip()
    if not source or not proposed:
        return ""
    if proposed in source:
        return proposed

    source_key, positions = _canonical_text(source, with_positions=True)
    proposed_key, _ = _canonical_text(proposed, with_positions=False)
    if not proposed_key:
        return ""
    normalized_start = source_key.find(proposed_key)
    if normalized_start >= 0:
        start = positions[normalized_start]
        end = positions[normalized_start + len(proposed_key) - 1] + 1
        return source[start:end].strip()

    candidates = _source_clauses(source)
    best_text = ""
    best_score = 0.0
    for candidate in candidates:
        candidate_key, _ = _canonical_text(candidate, with_positions=False)
        if not candidate_key:
            continue
        score = SequenceMatcher(None, proposed_key, candidate_key).ratio()
        if score > best_score:
            best_text = candidate.strip()
            best_score = score
    # 轻微措辞和数字形式变化可以恢复；明显无关的模型文本仍不能进入评分。
    return best_text if best_score >= 0.62 else ""


def _canonical_text(value: str, *, with_positions: bool) -> tuple[str, list[int]]:
    chars: list[str] = []
    positions: list[int] = []
    for index, raw_char in enumerate(value):
        for char in unicodedata.normalize("NFKC", raw_char).casefold():
            if char.isalnum():
                chars.append(char)
                if with_positions:
                    positions.append(index)
    return "".join(chars), positions


def _source_clauses(source: str) -> list[str]:
    """同时提供整句和短分句候选，兼容模型只轻微概括长句中的一个评价。"""
    candidates: list[str] = []
    seen: set[str] = set()
    for pattern in (r"[^。！？；\n]+[。！？；]?", r"[^，,。！？；\n]+[，,。！？；]?"):
        for match in re.finditer(pattern, source):
            value = match.group(0).strip()
            if value and value not in seen:
                seen.add(value)
                candidates.append(value)
    return candidates


def _records(values: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("record_id") or row.get("recordId")): dict(row)
        for row in values
        if isinstance(row, Mapping) and (row.get("record_id") or row.get("recordId"))
    }


def _embedded(records: Mapping[str, Mapping[str, Any]], key: str) -> Any:
    values = [_mapping(row.get("payload")).get(key) for row in records.values()]
    values = [value for value in values if value is not None]
    return values[0] if len(values) == 1 else None


def _record_text(record: Mapping[str, Any] | None) -> str:
    if not record:
        return ""
    payload = _mapping(record.get("payload"))
    return str(
        record.get("analysis_text")
        or
        record.get("raw_text")
        or record.get("answer")
        or record.get("response")
        or record.get("notes")
        or record.get("text")
        or payload.get("raw_text")
        or payload.get("text")
        or ""
    )


def _normalise_refs(value: Any, record_id: str, raw_text: str) -> list[dict[str, Any]]:
    rows = [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
    valid = [
        {**item, "record_id": record_id, "quote": str(item.get("quote") or raw_text)}
        for item in rows
        if str(item.get("quote") or raw_text) in raw_text
    ]
    return valid or [{"record_id": record_id, "quote": raw_text}]


def _attach_segment_ref(values: Sequence[Mapping[str, Any]], segment_id: str) -> list[dict[str, Any]]:
    return [{**dict(value), "segment_id": segment_id} for value in values]


def _first_quote(values: Sequence[Mapping[str, Any]]) -> str:
    return next((str(item.get("quote") or "").strip() for item in values if str(item.get("quote") or "").strip()), "")


def _refs_valid(values: Any, segments: Mapping[str, InterviewSegment]) -> bool:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        return False
    for row in values:
        if not isinstance(row, Mapping):
            return False
        quote = str(row.get("quote") or "")
        segment = segments.get(str(row.get("segment_id") or ""))
        if not quote or segment is None or quote not in segment.raw_text:
            return False
    return True


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
