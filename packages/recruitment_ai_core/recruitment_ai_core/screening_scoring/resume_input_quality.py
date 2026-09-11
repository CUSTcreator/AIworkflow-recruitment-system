from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .contracts import CandidateSpan, ExperienceUnit, ResumeInputQualityReport, SourceBullet


class ResumeInputQualityError(RuntimeError):
    def __init__(self, report: ResumeInputQualityReport):
        self.report = report
        super().__init__(
            "resume_input_quality_rejected:"
            + ",".join(report.warnings or ["unusable_resume_text"])
        )


def evaluate_resume_input_quality(
    text: str,
    *,
    spans: list[CandidateSpan],
    experience_units: list[ExperienceUnit],
    source_bullets: list[SourceBullet],
    section_headers_found: Iterable[str],
) -> ResumeInputQualityReport:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    character_count = len(re.sub(r"\s+", "", text))
    line_count = len(lines)
    long_line_ratio = _ratio(sum(len(line) >= 300 for line in lines), line_count)
    duplicate_line_ratio = _duplicate_ratio(lines)
    garbled_ratio = _garbled_ratio(text)
    assigned_chars = sum(
        len(re.sub(r"\s+", "", span.text))
        for span in spans
        if span.section != "other" or span.experience_unit_id
    )
    unassigned_ratio = 1.0 - _ratio(assigned_chars, character_count) if character_count else 1.0
    headers = sorted(set(section_headers_found))
    warnings: list[str] = []

    if character_count < 40:
        warnings.append("resume_text_too_short")
    if not headers:
        warnings.append("section_headers_missing")
    if not experience_units:
        warnings.append("experience_project_missing")
    if not source_bullets:
        warnings.append("scorable_experience_missing")
    if long_line_ratio > 0:
        warnings.append("suspicious_long_line")
    if duplicate_line_ratio >= 0.10:
        warnings.append("duplicate_lines")
    if garbled_ratio > 0:
        warnings.append("garbled_characters")
    if unassigned_ratio >= 0.60:
        warnings.append("high_unassigned_text_ratio")
    if any(_title_contains_action(unit.title) for unit in experience_units) or any(
        span.section == "experience"
        and _title_contains_action(span.text)
        for span in spans
    ):
        # “项目名 + 日期 + 动作”挤在同一行时，边界层会拒绝把整行当标题；
        # 质量报告仍需保留告警，提示后续修复不要静默吞掉标题中的工作事实。
        warnings.append("project_title_may_contain_work_fact")
    if _has_suspicious_section_reentry(spans):
        warnings.append("suspicious_section_order")

    severe_long_single_line = line_count == 1 and character_count >= 800
    severe_garbled = garbled_ratio >= 0.20
    unusably_short = character_count < 10
    status = "reject" if unusably_short or severe_garbled or severe_long_single_line else ("warning" if warnings else "pass")
    return ResumeInputQualityReport(
        status=status,
        character_count=character_count,
        nonempty_line_count=line_count,
        section_headers_found=headers,
        project_count=len(experience_units),
        scorable_source_count=len(source_bullets),
        suspicious_long_line_ratio=round(long_line_ratio, 4),
        duplicate_line_ratio=round(duplicate_line_ratio, 4),
        garbled_character_ratio=round(garbled_ratio, 4),
        unassigned_text_ratio=round(max(0.0, unassigned_ratio), 4),
        warnings=warnings,
    )


def _duplicate_ratio(lines: list[str]) -> float:
    normalized = [re.sub(r"\s+", "", line) for line in lines if len(re.sub(r"\s+", "", line)) >= 4]
    if not normalized:
        return 0.0
    counts = Counter(normalized)
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    return _ratio(duplicates, len(normalized))


def _garbled_ratio(text: str) -> float:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    invalid = sum(
        char == "\ufffd" or (ord(char) < 32 and char not in "\n\r\t")
        for char in compact
    )
    return _ratio(invalid, len(compact))


def _title_contains_action(text: str) -> bool:
    if not re.search(r"(?:19|20)\d{2}", text):
        return False
    action_pattern = re.compile(
        r"负责(?!人)|主导|参与|实现|构建|优化|完成|搭建|落地|部署"
    )
    action_segments = [
        segment.strip()
        for segment in re.split(r"[|｜]", text)
        if action_pattern.search(segment)
    ]
    if not action_segments:
        return False
    role_only_pattern = re.compile(
        r"(?:独立|个人|团队)?完成"
        r"|(?:核心|主要)?(?:参与|负责)"
        r"|(?:项目)?负责人"
    )
    normalized_segments = [
        re.sub(
            r"(?:19|20)\d{2}(?:[./-]\d{1,2})?"
            r"(?:\s*[-—至]\s*(?:19|20)?\d{2}(?:[./-]\d{1,2})?)?",
            "",
            segment,
        ).strip(" -—·")
        for segment in action_segments
    ]
    return any(
        segment and not role_only_pattern.fullmatch(segment)
        for segment in normalized_segments
    )


def _has_suspicious_section_reentry(spans: list[CandidateSpan]) -> bool:
    sequence: list[str] = []
    for span in spans:
        if span.section not in {"experience", "skills", "education"}:
            continue
        if not sequence or span.section != sequence[-1]:
            sequence.append(span.section)
    seen: set[str] = set()
    for section in sequence:
        if section in seen and section in {"experience", "skills", "education"}:
            return True
        seen.add(section)
    return False


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
