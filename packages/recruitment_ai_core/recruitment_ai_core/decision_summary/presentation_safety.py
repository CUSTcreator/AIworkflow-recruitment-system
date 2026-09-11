"""面向招聘人员的展示文案安全规则。"""
from __future__ import annotations

import re

# 展示层禁止暴露实现细节、模型等级、内部字段和裸评分。
_FORBIDDEN = (
    re.compile(r"\b(?:JDC|JCR|PIR|WUIR|PAIR|IT|ASV|APP|CAN|RES|SRC|WF|ART)[_-][A-Za-z0-9_-]+\b", re.I),
    re.compile(r"\b(?:candidate|application|resume|source|workflow|artifact|signal|target|trigger|schema|json|payload|snapshot|hash)[_-]?[A-Za-z0-9]*\b", re.I),
    re.compile(r"(?<![A-Za-z])(?:L|P|S|T|V)\s*\d+(?:\.\d+)?(?![A-Za-z])", re.I),
    re.compile(r"\b(?:level|tier|rank|score|confidence|severity|priority)\s*[:=]?\s*\d*(?:\.\d+)?\b", re.I),
    re.compile(r"(?:等级|级别|置信度|分数|评分|得分|百分比|命中规则|规则结论|模型输出|系统判断|字段|对象|流程|工作流|JSON|Schema|触发码)", re.I),
    re.compile(r"(?:\d+(?:\.\d+)?\s*(?:分|分数|%|百分号)|\b\d+(?:\.\d+)?\s*/\s*\d+\b)"),
    re.compile(r"`[^`]+`|\b[a-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b"),
)


def is_safe_display_text(value: object) -> bool:
    """判断一段文字是否可以直接显示给招聘人员。"""
    text = str(value or "").strip()
    return bool(text) and not any(pattern.search(text) for pattern in _FORBIDDEN)


def safe_display_text(value: object, fallback: str, limit: int) -> str:
    """安全地截断展示文本；命中内部术语时使用通俗兜底文案。"""
    text = str(value or "").strip()
    if not text or len(text) > limit or not is_safe_display_text(text):
        return fallback[:limit]
    return text

