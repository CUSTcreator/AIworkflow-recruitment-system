from __future__ import annotations

"""Resolve resume regions from short section anchors before field extraction.

Only short heading-like blocks may use fuzzy matching. Body text is never
matched fuzzily, because a distant keyword hit must not move evidence across
education, experience, skills, honors, or attachment regions.
"""

from dataclasses import dataclass
import re
from typing import Any, Iterable


SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "profile": (
        "基础信息", "个人信息", "基本信息", "家庭情况", "附加信息", "其他信息",
        "简历附件", "自我评价", "个人特长", "个人特长自我评价", "求职意向", "个人评价",
    ),
    "education": ("教育背景", "教育经历", "学习经历", "学历教育"),
    "skills": (
        "专业技能", "技能清单", "技能专长", "核心技能", "技术技能", "软件能力",
        "工具能力", "技术能力", "专业能力", "计算机技能", "软件技能", "熟悉工具",
    ),
    "experience": (
        "工作经历", "实习经历", "实习经验", "项目经历", "项目经验", "项目实践",
        "在校项目", "校园项目", "个人项目", "实践经历", "科研经历",
    ),
    "honors": (
        "奖励荣誉", "荣誉奖项", "获奖经历", "在校荣誉", "获奖情况", "奖励情况",
        "荣誉情况",
    ),
    "research": ("论文专著", "发表论文", "学术成果", "科研成果", "专利成果"),
    "other": ("在校职务", "学生工作", "校园经历", "社会职务", "社会实践"),
    "qualification": ("资格证书", "职业证书", "证书资质", "语言能力"),
}


@dataclass(frozen=True, slots=True)
class ResumeBlockFeature:
    line_number: int
    block_id: str
    text: str
    normalized_text: str
    section_anchor: str | None
    section: str
    page: Any = None
    bbox: tuple[float, float, float, float] | None = None


def resolve_section_anchor(text: str) -> str | None:
    """Return a section only when *text* looks like a short heading."""
    normalized = _normalize_heading(text)
    if not normalized:
        return None
    for section, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return section

    # A labelled value or a bilingual heading is still a local anchor. Merely
    # starting with the same words is insufficient: "项目经历丰富" is prose.
    for section, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            suffix = normalized[len(alias):] if normalized.startswith(alias) else ""
            labelled = re.match(
                rf"^\s*{re.escape(alias)}\s*[：:]",
                str(text or ""),
            )
            bilingual = bool(suffix and re.fullmatch(r"[A-Za-z]+", suffix))
            if len(normalized) <= len(alias) + 10 and (labelled or bilingual):
                return section

    if not _is_fuzzy_heading_candidate(text, normalized):
        return None
    best_section: str | None = None
    best_distance = 99
    for section, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            distance = _edit_distance(normalized, alias)
            if distance < best_distance:
                best_section = section
                best_distance = distance
    # One OCR character may be wrong or missing. Requiring headings of at least
    # four characters avoids fuzzy matches such as ordinary two-character words.
    return best_section if len(normalized) >= 4 and best_distance <= 1 else None


def build_block_features(blocks: Iterable[dict[str, Any]]) -> list[ResumeBlockFeature]:
    ordered = sorted(
        (dict(item) for item in blocks),
        key=lambda item: int(item.get("order") or 0),
    )
    # First discover all anchors, then assign ranges.  Keeping discovery
    # separate from assignment prevents a body keyword from greedily changing
    # the section while still allowing table-internal headings to define a
    # boundary for all following logical rows.
    anchors = [
        _block_section_anchor(block)
        for block in ordered
    ]
    current_section = "other"
    output: list[ResumeBlockFeature] = []
    for line_number, (block, anchor) in enumerate(zip(ordered, anchors), start=1):
        text = str(block.get("text") or "").strip()
        if anchor is not None:
            current_section = anchor
        output.append(
            ResumeBlockFeature(
                line_number=line_number,
                block_id=str(block.get("block_id") or ""),
                text=text,
                normalized_text=_normalize_heading(text),
                section_anchor=anchor,
                section=current_section,
                page=block.get("page"),
                bbox=_bbox(block.get("bbox")),
            )
        )
    return output


def _block_section_anchor(block: dict[str, Any]) -> str | None:
    text_anchor = resolve_section_anchor(str(block.get("text") or ""))
    if text_anchor is not None:
        return text_anchor
    field_label = str(block.get("field_label") or "").strip()
    return resolve_section_anchor(field_label) if field_label else None


def section_by_line(blocks: Iterable[dict[str, Any]]) -> dict[int, str]:
    features = build_block_features(blocks)
    if not any(item.section_anchor is not None for item in features):
        return {}
    return {item.line_number: item.section for item in features}


def text_section_ranges(text: str) -> tuple[list[str], dict[int, str], bool]:
    """Return source lines, their nearest preceding section, and anchor presence."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    pseudo_blocks = [
        {"block_id": f"LINE_{index:04d}", "order": index, "text": line}
        for index, line in enumerate(lines, start=1)
    ]
    features = build_block_features(pseudo_blocks)
    return lines, {item.line_number: item.section for item in features}, any(
        item.section_anchor is not None for item in features
    )


def quote_is_in_section(text: str, quote: str, expected_section: str) -> bool:
    lines, sections, has_anchors = text_section_ranges(text)
    if not has_anchors:
        return False
    offset = text.find(quote)
    if offset < 0:
        return False
    start_line = text[:offset].count("\n") + 1
    end_line = start_line + quote.count("\n")
    relevant = [
        sections.get(line_number, "other")
        for line_number in range(start_line, min(end_line, len(lines)) + 1)
        if lines[line_number - 1].strip()
    ]
    return bool(relevant) and all(item == expected_section for item in relevant)


def has_section_anchor(text: str, section: str) -> bool:
    lines, _, _ = text_section_ranges(text)
    return any(resolve_section_anchor(line) == section for line in lines)


def _normalize_heading(text: str) -> str:
    return re.sub(r"[\s：:／/|｜·•_\-—–]+", "", str(text or "")).strip()


def _is_fuzzy_heading_candidate(text: str, normalized: str) -> bool:
    stripped = str(text or "").strip()
    if not (4 <= len(normalized) <= 12):
        return False
    if re.search(r"[。！？!?；;,，]|(?:19|20)\d{2}|@|https?://", stripped):
        return False
    return not re.search(r"(?:负责|参与|完成|实现|开发|设计|获得|担任|就读)", stripped)


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None
