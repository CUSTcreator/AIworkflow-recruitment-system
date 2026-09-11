"""V2/V3 面评语义解析的纯算法管线。

本包只定义并编排算法层的输入、输出和步骤边界。它不导入后端 ORM、
数据库会话或 HTTP DTO；由后端 Workflow 负责冻结来源、保存检查点和发布结果。
"""

from .contracts import (
    InterviewEvidenceExtractionResult,
    InterviewEvidenceItem,
    InterviewParseDraft,
    InterviewParseInput,
    InterviewSegment,
    ParseValidationIssue,
    ParseValidationResult,
)
from .pipeline import (
    extract_and_bind_interview_evidence,
    has_interview_evidence,
    normalize_interview_evidence,
    repair_interview_parse_draft,
    validate_interview_parse_draft,
)

__all__ = [
    "InterviewEvidenceExtractionResult",
    "InterviewEvidenceItem",
    "InterviewParseDraft",
    "InterviewParseInput",
    "InterviewSegment",
    "ParseValidationIssue",
    "ParseValidationResult",
    "extract_and_bind_interview_evidence",
    "has_interview_evidence",
    "normalize_interview_evidence",
    "repair_interview_parse_draft",
    "validate_interview_parse_draft",
]
