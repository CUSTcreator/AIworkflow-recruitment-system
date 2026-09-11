from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from recruitment_ai_core.llm import call_json_llm
from recruitment_ai_core.resume_structuring.logical_block_normalizer import (
    expand_logical_block,
    source_block_id,
)
from recruitment_ai_core.screening_scoring.section_resolver import section_by_line


@dataclass(slots=True)
class ExtractedResumeMetadata:
    candidate_name: str
    phone: str | None
    email: str | None
    current_title: str | None
    relevant_experience_years: float | None
    education_records: list[dict[str, Any]]
    qualification_records: list[dict[str, Any]]
    age: int | None
    metadata: dict[str, Any]

    @property
    def years_of_experience(self) -> str | None:
        value = self.relevant_experience_years
        return f"{value:g}年" if isinstance(value, (int, float)) else None

    @property
    def education(self) -> str | None:
        rows = [
            " ".join(
                str(item.get(key) or "").strip()
                for key in ("school", "major", "degree")
                if str(item.get(key) or "").strip()
            )
            for item in self.education_records
        ]
        text = "；".join(item for item in rows if item)
        return text or None

    @property
    def school(self) -> str | None:
        return _highest_education_value(self.education_records, "school")

    @property
    def major(self) -> str | None:
        return _highest_education_value(self.education_records, "major")

    @property
    def highest_degree(self) -> str | None:
        return _highest_education_value(self.education_records, "degree")

    @property
    def graduation_year(self) -> int | None:
        value = _highest_education_value(self.education_records, "graduation_year")
        return value if isinstance(value, int) else None


RESUME_METADATA_FIELDS = (
    "candidate_name", "phone", "email", "current_title",
    "relevant_experience_years", "age",
)

# 教育年份允许少量未来日期（例如“预计毕业”），但不接受远超当前年份的
# 模型幻觉。该上限同时用于 JSON Schema 和后端清洗，避免请求与验收规则漂移。
_MAX_EDUCATION_YEAR = date.today().year + 8


RESUME_DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        *RESUME_METADATA_FIELDS,
        "education_records",
        "qualification_records",
        "field_source_block_ids",
    ],
    "properties": {
        "candidate_name": {"type": ["string", "null"]},
        "phone": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
        "current_title": {"type": ["string", "null"]},
        "relevant_experience_years": {"type": ["number", "null"], "minimum": 0, "maximum": 80},
        "age": {"type": ["integer", "null"], "minimum": 14, "maximum": 100},
        "education_records": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "school", "major", "degree", "status", "start_year",
                    "graduation_year", "source_block_ids",
                ],
                "properties": {
                    "school": {"type": ["string", "null"]},
                    "major": {"type": ["string", "null"]},
                    "degree": {"type": ["string", "null"]},
                    "status": {"enum": ["completed", "in_progress", "unknown"]},
                    "start_year": {"type": ["integer", "null"], "minimum": 1950, "maximum": _MAX_EDUCATION_YEAR},
                    "graduation_year": {"type": ["integer", "null"], "minimum": 1950, "maximum": _MAX_EDUCATION_YEAR},
                    "source_block_ids": {
                        # 空数组表示模型无法可靠绑定来源；该条记录会被局部丢弃，
                        # 不应因此让同一响应中的其他教育记录全部失效。
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "qualification_records": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_block_ids"],
                "properties": {
                    "source_block_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "field_source_block_ids": {
            "type": "object",
            "additionalProperties": False,
            "required": list(RESUME_METADATA_FIELDS),
            "properties": {
                field: {"type": "array", "items": {"type": "string"}}
                for field in RESUME_METADATA_FIELDS
            },
        },
    },
}


class ResumeDocumentProcessor:
    def extract_deterministic(
        self,
        text: str,
        *,
        logical_blocks: list[dict[str, Any]] | None = None,
    ) -> ExtractedResumeMetadata:
        """Extract basic fields without an LLM and retain field-level sources."""
        return self._fallback_extract(_metadata_source_blocks(logical_blocks, text))

    def extract(
        self,
        text: str,
        *,
        candidate_name_override: str | None = None,
        logical_blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[ExtractedResumeMetadata, dict[str, Any]]:
        model_blocks = _metadata_source_blocks(logical_blocks, text)
        parsed, trace = call_json_llm(
            workflow_name="resume_document_extraction",
            schema_name="resume_document_extraction_v2",
            json_schema=RESUME_DOCUMENT_SCHEMA,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "从简历原文提取候选人姓名、手机号、邮箱、当前身份或职位、相关工作年限、年龄，"
                        "以及全部高等教育经历、证书、职业资格、认证、资质和明确写出的语言等级。"
                        "本科、硕士、博士等每段教育经历都必须分别放入"
                        "education_records，不得只返回最高学历。每条记录必须对应同一学校的"
                        "一次完整就读经历，必须包含学校，并至少包含专业或学历。最高学历、"
                        "最高学位、排名、获奖、社团、课程等摘要或附属信息不得单独形成记录。"
                        "简历存在教育栏目时，education_records只能引用该栏目内的原文；"
                        "研究生会、学生会、获奖信息、自我评价、附件名和成绩单文件名即使含有"
                        "学校或学历词，也绝不是教育记录。"
                         "degree保留原文学历名称，"
                         "status只能根据原文明示的毕业、在读或预计毕业信息判断。"
                         "start_year和graduation_year只能填写原文出现且在1950年至当前年份后8年内的年份；"
                         "异常年份、无法确认的年份返回null，不得据此新建教育记录。"
                         "年龄只能提取简历明确写出的年龄，不得根据毕业年份推算。"
                        "不得补写原文没有的信息；不确定时返回 null。"
                        "每个source_block_id只能从输入source_blocks的block_id中选择，不得编造ID。"
                        "field_source_block_ids为每个基础字段给出能够支持字段值的最少Block ID；"
                        "education_records中每条记录使用source_block_ids引用同一次就读经历涉及的Block。"
                        "qualification_records每条只返回source_block_ids，引用能够共同表达一项资格事实的"
                        "最少Block，例如字段标签“英语等级”和字段值“六级”；不得把项目中使用的"
                        "工具、普通技能、自我评价或培训意向当作资格记录。"
                        "输入中的表格和普通文本都已整理为逻辑字段，同一原始表格中的多个逻辑字段"
                        "也有各自的block_id。非空字段至少引用一个Block，空字段返回空数组。"
                        "只输出Schema规定的字段，不要输出解释或其他字段。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"source_blocks": _llm_source_blocks(model_blocks)},
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        fallback = self._fallback_extract(model_blocks)
        if parsed is None:
            result = fallback
            mode = "deterministic_fallback"
        else:
            sanitized, dropped, unverified = self._sanitize_llm_payload(parsed, model_blocks)
            candidate_name = self._clean(sanitized.get("candidate_name"))
            education_records = _deduplicate_education_records(
                list(sanitized.get("education_records") or [])
            )
            result = ExtractedResumeMetadata(
                candidate_name=candidate_name or fallback.candidate_name,
                phone=self._phone(sanitized.get("phone")) or fallback.phone,
                email=self._email(sanitized.get("email")) or fallback.email,
                current_title=self._clean(sanitized.get("current_title"))
                or fallback.current_title,
                relevant_experience_years=(
                    self._bounded_float(
                        sanitized.get("relevant_experience_years"), 0, 80
                    )
                    if sanitized.get("relevant_experience_years") is not None
                    else None
                ),
                education_records=education_records,
                qualification_records=list(
                    sanitized.get("qualification_records") or []
                ),
                age=self._bounded_int(sanitized.get("age"), 14, 100) or fallback.age,
                metadata={
                    "field_source_refs": _merge_field_source_refs(
                        dict(sanitized.get("field_source_refs") or {}),
                        dict(fallback.metadata.get("field_source_refs") or {}),
                        prefer_fallback_for={
                            field
                            for field in RESUME_METADATA_FIELDS
                            if sanitized.get(field) is None
                        },
                    ),
                    "dropped_invalid_fields": dropped,
                    "unverified_fields": unverified,
                },
            )
            mode = "llm_partial" if dropped else "llm"
        if candidate_name_override and candidate_name_override.strip():
            result.candidate_name = candidate_name_override.strip()
            result.metadata = {
                **result.metadata,
                "candidate_name_source": "upload_override",
            }
        return result, {"mode": mode, "llm_trace": trace}

    def _fallback_extract(self, blocks: list[dict[str, Any]]) -> ExtractedResumeMetadata:
        """Recover only explicitly labelled or format-exact display fields."""
        candidate_name, candidate_refs = _labelled_value(
            blocks, {"姓名"}, validator=_valid_candidate_name
        )
        current_title, title_refs = _labelled_value(
            blocks, {"求职意向", "目标岗位", "应聘岗位", "当前职位", "职位"}
        )
        age_text, age_refs = _labelled_value(
            blocks,
            {"年龄", "年齢"},
            validator=lambda value: bool(re.fullmatch(r"\d{2}\s*岁?", value)),
        )
        phone, phone_refs = _formatted_value(
            blocks,
            re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9](?:[-\s]?\d){9}(?!\d)"),
        )
        email, email_refs = _formatted_value(
            blocks,
            re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        )
        return ExtractedResumeMetadata(
            candidate_name=candidate_name or "",
            phone=self._phone(phone),
            email=self._email(email),
            current_title=current_title,
            # 降级只恢复联系方式等展示信息。硬筛事实必须来自基础信息 LLM
            # 或用户校正，不能由关键词规则在失败路径中补写。
            relevant_experience_years=None,
            education_records=[],
            qualification_records=[],
            age=self._bounded_int(age_text, 14, 100),
            metadata={
                "field_source_refs": {
                    "candidate_name": candidate_refs,
                    "phone": phone_refs,
                    "email": email_refs,
                    "current_title": title_refs,
                    "relevant_experience_years": [],
                    "age": age_refs,
                }
            },
        )

    @staticmethod
    def _phone(value: Any) -> str | None:
        digits = re.sub(r"\D", "", str(value or ""))
        if digits.startswith("86") and len(digits) == 13:
            digits = digits[2:]
        return digits if re.fullmatch(r"1[3-9]\d{9}", digits) else None

    @staticmethod
    def _email(value: Any) -> str | None:
        value = str(value or "").strip().casefold()
        return value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else None

    def _sanitize_llm_payload(
        self, payload: dict[str, Any], blocks: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """逐字段、逐学历记录校验来源，保留同一响应中的其余正确事实。

        来源 Block ID 是不可变证据链的边界；引用文本与模型规范化值的逐字
        相等不是发布前提，因为 MinerU/HTML/Markdown 的换行、表格拼接和标点
        会改变表面文本。值不一致只记为 ``unverified``，不再用它否决 LLM
        已经提取出的基础事实。
        """
        raw_sources = payload.get("field_source_block_ids")
        sources = dict(raw_sources) if isinstance(raw_sources, dict) else {}
        sanitized: dict[str, Any] = {"field_source_refs": {}}
        dropped: list[str] = []
        unverified: list[str] = []
        for field in RESUME_METADATA_FIELDS:
            value = payload.get(field)
            refs = _source_refs(sources.get(field), blocks)
            if value is None:
                sanitized[field] = None
                sanitized["field_source_refs"][field] = []
            elif field == "relevant_experience_years" and (
                not refs or not _value_supported_by_refs(field, value, refs)
            ):
                # 年限是硬筛事实，数字本身不够作为来源。教育年份、学院名称
                # 或其他数字不能支撑工作年限；该字段局部置空，不阻断其余事实。
                sanitized[field] = None
                sanitized["field_source_refs"][field] = []
                unverified.append(field)
            elif refs:
                sanitized[field] = value
                sanitized["field_source_refs"][field] = refs
                if not _value_supported_by_refs(field, value, refs):
                    unverified.append(field)
            else:
                # LLM 字段即使没有可绑定的 Block 也尽量保留。这样不会因为
                # 版面解析丢失来源 ID 而把姓名/联系方式改成错误值；调用方可
                # 依据 unverified_fields 展示“待确认”，而不是静默猜测或阻断。
                sanitized[field] = value
                sanitized["field_source_refs"][field] = []
                unverified.append(field)

        records: list[dict[str, Any]] = []
        for index, raw in enumerate(list(payload.get("education_records") or [])):
            record = self._sanitize_education_record(raw, blocks)
            if record is None:
                dropped.append(f"education_records[{index}]")
                continue
            records.append(record)
        sanitized["education_records"] = records
        qualifications: list[dict[str, Any]] = []
        for index, raw in enumerate(list(payload.get("qualification_records") or [])):
            record = self._sanitize_qualification_record(raw, blocks)
            if record is None:
                dropped.append(f"qualification_records[{index}]")
                continue
            qualifications.append(record)
        sanitized["qualification_records"] = qualifications
        return sanitized, dropped, unverified

    @staticmethod
    def _sanitize_qualification_record(
        raw: Any, blocks: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Keep an LLM classification backed by known logical source Blocks."""
        if not isinstance(raw, dict):
            return None
        refs = _source_refs(raw.get("source_block_ids"), blocks)
        if not refs:
            return None
        return {"source_refs": refs}

    def _sanitize_education_record(
        self, raw: Any, blocks: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        source_ids = {
            str(item).strip()
            for item in list(raw.get("source_block_ids") or [])
            if str(item).strip()
        }
        refs = _source_refs(list(source_ids), blocks)
        if not refs:
            return None
        # LLM 有时会把同一教育条目旁边的“成绩单附件”一并引用。附件本身
        # 不能证明学历，但也不能因此否决同组中明确的学校、专业、学历和日期。
        # 这里只移除纯附件/荣誉引用；若一个混合表格 Block 自身仍包含明确
        # 就读事实则保留，避免 MinerU 表格合并造成整条教育记录被误删。
        refs = [
            ref
            for ref in refs
            if not _is_non_education_evidence(ref["quote"])
            or _has_explicit_education_enrollment(ref["quote"])
        ]
        if not refs:
            return None
        joined = "\n".join(item["quote"] for item in refs)
        school = self._clean(raw.get("school"))
        major = self._clean(raw.get("major"))
        degree = self._clean(raw.get("degree"))
        # 一条可发布的教育事实必须能标识具体学校和就读层次/专业；只有“硕士”
        # 或“最高学历”等碎片即使逐字出现在原文，也不能成为独立教育记录。
        if not school or not any((major, degree)):
            return None
        # 不做 HTML/原文逐字包含校验。表格拆行、OCR 空格和模型规范化会让
        # “学校名称（校区）”等合法值与 quote 表面不同；Block ID 已经提供
        # 了不可变来源边界，严格文本比较只会误伤正确的 LLM 结果。
        # 但教育记录仍需有一个软锚点，避免模型把同一教育 Block 幻觉成
        # 完全无关的学校和专业。软锚点允许括号、空格、标点和常见后缀差异，
        # 只在学校与专业都完全脱离来源时局部丢弃该条记录。
        if not (
            _soft_value_supported(school, joined)
            or _soft_value_supported(major, joined)
        ):
            return None
        raw_start_year = raw.get("start_year")
        raw_graduation_year = raw.get("graduation_year")
        start_year = self._bounded_int(raw_start_year, 1950, _MAX_EDUCATION_YEAR)
        graduation_year = self._bounded_int(
            raw_graduation_year, 1950, _MAX_EDUCATION_YEAR
        )
        # 年份字段一旦被模型填写，就必须是可接受范围内的真实年份；不能把
        # 明确的异常年份静默改成缺失后继续发布这条教育记录。
        if raw_start_year is not None and start_year is None:
            return None
        if raw_graduation_year is not None and graduation_year is None:
            return None
        years_in_source = {int(item) for item in re.findall(r"(?:19|20)\d{2}", joined)}
        if start_year is not None and start_year not in years_in_source:
            return None
        if graduation_year is not None and graduation_year not in years_in_source:
            return None
        if (
            start_year is not None
            and graduation_year is not None
            and start_year > graduation_year
        ):
            return None
        status = str(raw.get("status") or "unknown")
        if status not in {"completed", "in_progress", "unknown"}:
            status = "unknown"
        return {
            "school": school,
            "major": major,
            "degree": degree,
            "status": status,
            "start_year": start_year,
            "graduation_year": graduation_year,
            "source_refs": refs,
        }

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if minimum <= parsed <= maximum else None

    @staticmethod
    def _bounded_float(value: Any, minimum: float, maximum: float) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        if match is None:
            return None
        parsed = float(match.group())
        return parsed if minimum <= parsed <= maximum else None


def _value_supported_by_refs(
    field: str, value: Any, refs: list[dict[str, str]]
) -> bool:
    joined = "\n".join(item["quote"] for item in refs)
    if field == "phone":
        return re.sub(r"\D", "", str(value)) in re.sub(r"\D", "", joined)
    if field == "email":
        return str(value).strip().casefold() in joined.casefold()
    if field == "relevant_experience_years":
        if not _is_work_experience_source_refs(refs):
            return False
        expected = float(value)
        return any(float(item) == expected for item in re.findall(r"\d+(?:\.\d+)?", joined))
    if field == "age":
        expected = float(value)
        return any(float(item) == expected for item in re.findall(r"\d+(?:\.\d+)?", joined))
    return _compact(str(value)) in _compact(joined)


def _is_work_experience_source_refs(refs: list[dict[str, str]]) -> bool:
    """Accept only source text that semantically describes work duration."""
    joined = "\n".join(str(item.get("quote") or "") for item in refs)
    compact = _compact(joined)
    if not compact or re.search(
        r"(?:教育经历|教育背景|学习经历|学校|学院|大学|专业|学历|学位|"
        r"本科|硕士|博士|研究生|学士|大专|专科|毕业|在读)",
        compact,
    ):
        return False
    return bool(re.search(
        r"(?:工作经验|相关经验|工作年限|从业|任职|工作经历|实习经历|工作经验|"
        r"工作\d+(?:\.\d+)?年|从事[^\n]{0,20}\d+(?:\.\d+)?年|"
        r"(?:19|20)\d{2}[^\n]{0,20}(?:至|到|[-~～])[^\n]{0,20}(?:19|20)\d{2}|"
        r"(?:19|20)\d{2}[^\n]{0,20}(?:至今|现在))",
        compact,
        flags=re.IGNORECASE,
    ))


def _metadata_source_blocks(
    blocks: list[dict[str, Any]] | None, text: str
) -> list[dict[str, Any]]:
    if blocks:
        logical = [
            dict(item) for item in blocks if str(item.get("text") or "").strip()
        ]
    else:
        logical = []
        for index, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                logical.extend(
                    expand_logical_block({
                        "block_id": f"B_{index:04d}",
                        "order": index,
                        "text": line,
                        "block_type": "text",
                    })
                )
    for index, block in enumerate(logical, start=1):
        block["order"] = index
        block["block_id"] = str(block.get("block_id") or f"LB_{index:04d}")
    sections = section_by_line(logical)
    for index, block in enumerate(logical, start=1):
        block["section"] = sections.get(index, "other")
    return logical


def _llm_source_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "block_id": str(block.get("block_id") or ""),
            "section": str(block.get("section") or "other"),
            "field_label": str(block.get("field_label") or "") or None,
            "text": str(block.get("text") or ""),
        }
        for block in blocks
    ]


def _source_refs(value: Any, blocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return []
    by_id = {str(item.get("block_id") or ""): item for item in blocks}
    ids = list(dict.fromkeys(item.strip() for item in value if item.strip()))
    if not ids or any(item not in by_id for item in ids):
        return []
    return [
        {
            "block_id": source_block_id(by_id[item]),
            "quote": str(by_id[item].get("text") or "").strip(),
        }
        for item in ids
        if str(by_id[item].get("text") or "").strip()
    ]


def _merge_field_source_refs(
    primary: dict[str, Any],
    fallback: dict[str, Any],
    *,
    prefer_fallback_for: set[str],
) -> dict[str, list[dict[str, str]]]:
    return {
        field: list(
            (fallback if field in prefer_fallback_for else primary).get(field) or []
        )
        for field in RESUME_METADATA_FIELDS
    }


def _labelled_value(
    blocks: list[dict[str, Any]],
    labels: set[str],
    *,
    validator: Any = None,
) -> tuple[str | None, list[dict[str, str]]]:
    for block in blocks:
        label = str(block.get("field_label") or "").strip()
        value = str(block.get("field_value") or "").strip()
        if label not in labels or not value or (validator and not validator(value)):
            continue
        return value, _source_refs([str(block.get("block_id") or "")], blocks)
    return None, []


def _formatted_value(
    blocks: list[dict[str, Any]], pattern: re.Pattern[str]
) -> tuple[str | None, list[dict[str, str]]]:
    for block in blocks:
        match = pattern.search(str(block.get("text") or ""))
        if match:
            return match.group(), _source_refs(
                [str(block.get("block_id") or "")], blocks
            )
    return None, []


def _valid_candidate_name(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    return bool(
        re.fullmatch(
            r"[\u4e00-\u9fff·]{2,12}|[A-Za-z][A-Za-z .'-]{1,63}", compact
        )
    )


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _soft_value_supported(value: str | None, source: str) -> bool:
    """容忍版面规范化差异的来源锚点，不要求逐字 HTML 相等。"""
    compact_value = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "")).casefold()
    compact_source = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(source or "")).casefold()
    if not compact_value or not compact_source:
        return False
    if compact_value in compact_source or compact_source in compact_value:
        return True
    # 中文学校/专业名称在 OCR 中偶尔会丢掉一两个字；达到一半以上字符
    # 重合即可视为同一来源锚点，短值（如“硕士”）不参与此放宽判断。
    if len(compact_value) < 4:
        return False
    shared = sum(1 for char in set(compact_value) if char in compact_source)
    unique_length = len(set(compact_value))
    if shared < 3 or shared / max(1, unique_length) < 0.6:
        return False
    value_bigrams = {compact_value[index : index + 2] for index in range(len(compact_value) - 1)}
    source_bigrams = {compact_source[index : index + 2] for index in range(len(compact_source) - 1)}
    return len(value_bigrams & source_bigrams) / max(1, len(value_bigrams)) >= 0.5


def _is_non_education_evidence(value: str) -> bool:
    """Exclude organization, honor, self-review and attachment evidence."""
    compact = _compact(value)
    return bool(re.search(
        r"(?:研究生会|学生会|社团|协会|学生工作|校园活动|获奖|荣誉|奖学金|"
        r"自我评价|个人评价|附件|成绩单|证明材料|上传材料|简历文件|\.pdf|\.docx?)",
        compact,
        flags=re.IGNORECASE,
    ))


def _has_explicit_education_enrollment(value: str) -> bool:
    """Distinguish an enrollment fact from an attachment or honor filename."""
    compact = _compact(value)
    if re.search(
        r"(?:教育经历|教育背景|学习经历|学院名称|所学专业|专业课程|"
        r"学习形式|最高学历|最高学位|学位)",
        compact,
    ):
        return True
    has_degree = bool(re.search(r"(?:大专|专科|本科|学士|硕士|研究生|博士)", compact))
    has_period = bool(re.search(
        r"(?:19|20)\d{2}.{0,24}(?:至|到|[-~～]).{0,24}(?:19|20)\d{2}",
        compact,
    ))
    return has_degree and has_period


def _degree_rank(value: Any) -> int:
    text = str(value or "").casefold()
    if "博士" in text or "doctor" in text or "phd" in text:
        return 4
    if "硕士" in text or "研究生" in text or "master" in text:
        return 3
    if "本科" in text or "学士" in text or "bachelor" in text:
        return 2
    if "大专" in text or "专科" in text or "associate" in text:
        return 1
    return 0


def _highest_education_value(records: list[dict[str, Any]], field: str) -> Any:
    if not records:
        return None
    ordered = sorted(
        records,
        key=lambda item: (
            _degree_rank(item.get("degree")),
            int(item.get("graduation_year") or 0),
        ),
        reverse=True,
    )
    return next((item.get(field) for item in ordered if item.get(field) is not None), None)


def _deduplicate_education_records(
    verified: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并 LLM 返回的同一次就读经历，不引入规则提取的业务事实。"""
    output: list[dict[str, Any]] = []
    for source in verified:
        item = dict(source)
        existing = next(
            (row for row in output if _same_education_enrollment(row, item)),
            None,
        )
        if existing is None:
            output.append(item)
            continue
        for field in ("major", "degree", "status", "start_year", "graduation_year"):
            if existing.get(field) in {None, "", "unknown"} and item.get(field) not in {None, ""}:
                existing[field] = item[field]
        refs = [
            *list(existing.get("source_refs") or []),
            *list(item.get("source_refs") or []),
        ]
        existing["source_refs"] = list({
            (str(ref.get("block_id") or ""), str(ref.get("quote") or "")): ref
            for ref in refs
            if isinstance(ref, dict)
        }.values())
    return output


def _same_education_enrollment(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_school = str(left.get("school") or "").strip()
    right_school = str(right.get("school") or "").strip()
    if not _same_school_identity(left_school, right_school):
        return False
    left_rank = _degree_rank(left.get("degree"))
    right_rank = _degree_rank(right.get("degree"))
    if left_rank and right_rank and left_rank != right_rank:
        return False
    for field in ("start_year", "graduation_year"):
        if left.get(field) is not None and right.get(field) is not None and left[field] != right[field]:
            return False
    left_quotes = {
        _compact(str(value.get("quote") or ""))
        for value in list(left.get("source_refs") or [])
        if isinstance(value, dict)
    }
    right_quotes = {
        _compact(str(value.get("quote") or ""))
        for value in list(right.get("source_refs") or [])
        if isinstance(value, dict)
    }
    has_same_time = any(
        left.get(field) is not None and left.get(field) == right.get(field)
        for field in ("start_year", "graduation_year")
    )
    return bool(left_quotes & right_quotes) or has_same_time


def _same_school_identity(left: str, right: str) -> bool:
    """Match an explicit campus name with the same unqualified school name.

    Metadata LLMs may preserve ``(北京)`` while the deterministic extractor
    returns only the parent university.  Two different explicit campuses must
    remain distinct; one qualified and one unqualified spelling may match, and
    the caller still requires the same degree and enrollment year.
    """
    left_compact, right_compact = _compact(left), _compact(right)
    if not left_compact or not right_compact:
        return False
    if left_compact == right_compact:
        return True

    def identity(value: str) -> tuple[str, tuple[str, ...]]:
        qualifiers = tuple(
            _compact(item)
            for item in re.findall(r"[（(]([^）)]{1,24})[）)]", value)
            if not re.search(r"(?:211|985|双一流|重点|一流学科)", item)
        )
        base = re.sub(r"[（(][^）)]{1,24}[）)]", "", value)
        return _compact(base), qualifiers

    left_base, left_qualifiers = identity(left)
    right_base, right_qualifiers = identity(right)
    if not left_base or left_base != right_base:
        return False
    return not left_qualifiers or not right_qualifiers or left_qualifiers == right_qualifiers
