"""候选人简历处理读模型：提取展示所需的基础身份信息。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.app.models.entities import CandidateProfile
from backend.app.modules.document_ingestion.processors.resume_document_processor import (
    ResumeDocumentProcessor,
)


@dataclass(frozen=True, slots=True)
class CandidateBasicInfo:
    current_title: str
    years_of_experience: str
    age: int | None
    school: str
    major: str
    highest_degree: str


_PLACEHOLDERS = {"候选人", "待补充", "未提供", "未识别", "未知"}


def resolve_candidate_basic_info(
    profile: CandidateProfile | None = None,
    *,
    resume_text: str = "",
) -> CandidateBasicInfo:
    """优先读 CandidateProfile，缺失时才从当前 Submission 文本作展示降级。"""
    fallback = ResumeDocumentProcessor().extract_deterministic(
        resume_text or ""
    )
    # CandidateProfile 是当前业务字段的正式来源。
    return CandidateBasicInfo(
        current_title=_meaningful(profile.current_title if profile else None)
        or fallback.current_title
        or "",
        years_of_experience=_meaningful(profile.years_of_experience if profile else None)
        or fallback.years_of_experience
        or "",
        age=_age(profile.age if profile else None) or fallback.age,
        school=_meaningful(profile.school if profile else None) or fallback.school or "",
        major=_major(profile.major if profile else None) or _major(fallback.major),
        highest_degree=(
            _degree(profile.highest_degree if profile else None)
            or _degree(profile.education if profile else None)
            or fallback.highest_degree
            or ""
        ),
    )


def _meaningful(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in _PLACEHOLDERS else text


def _degree(value: Any) -> str:
    text = _meaningful(value)
    match = re.search(r"(博士|硕士|本科|学士|大专|专科)", text)
    if not match:
        return ""
    return "本科" if match.group(1) == "学士" else match.group(1)


def _major(value: Any) -> str:
    text = _meaningful(value).strip(" ·・,，;；:：()（）[]【】")
    text = text.replace("·", "").replace("・", "")
    if not text or len(text) > 40:
        return ""
    if re.match(
        r"^(?:专业技能|技能|课程|主修课程|成绩|GPA|项目|经历|职责|优先)",
        text,
        re.IGNORECASE,
    ):
        return ""
    if re.search(r"[。；;：:]|(?:负责|参与|实现|开发|构建|提升)", text):
        return ""
    if re.fullmatch(r"[A-Za-z][A-Za-z &+/-]{1,39}", text):
        return text
    return text if re.search(
        r"(技术|工程|科学|管理|设计|语言|教育|医学|经济|金融|"
        r"会计|数学|物理|化学|法学|智能|信息|自动化|统计|心理|"
        r"文学|历史|哲学|营销|传播)$",
        text,
    ) else ""


def _age(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if 14 <= parsed <= 100 else None
