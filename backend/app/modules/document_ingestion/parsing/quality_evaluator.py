from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .contracts import ParseQualityReport

class DocumentQualityEvaluator:
    def evaluate(
        self,
        text: str,
        *,
        page_count: int | None = None,
        expected_page_count: int | None = None,
        blocks: list[dict[str, Any]] | None = None,
        reference_blocks: list[dict[str, Any]] | None = None,
    ) -> ParseQualityReport:
        normalized = text.strip()
        reasons: list[str] = []
        warnings: list[str] = []
        if len(normalized) < 80:
            reasons.append("text_too_short")
        replacement_ratio = normalized.count("\ufffd") / max(1, len(normalized))
        if replacement_ratio > 0.01:
            reasons.append("too_many_replacement_characters")
        readable = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", normalized)
        readable_ratio = len(readable) / max(1, len(normalized))
        if readable_ratio < 0.35:
            reasons.append("low_readable_character_ratio")
        controls = [
            char
            for char in normalized
            if ord(char) < 32 and char not in {"\n", "\t"}
        ]
        control_ratio = len(controls) / max(1, len(normalized))
        if control_ratio > 0.005:
            reasons.append("too_many_control_characters")
        if page_count and len(normalized) / page_count < 40:
            reasons.append("too_little_text_per_page")
        if (
            expected_page_count
            and page_count
            and expected_page_count != page_count
        ):
            reasons.append("page_count_mismatch")
        duplicate_line_ratio = self._duplicate_line_ratio(normalized)
        if duplicate_line_ratio > 0.3:
            reasons.append("excessive_duplicate_lines")
        page_coverage_ratio = self._page_coverage(
            blocks or [], expected_page_count or page_count
        )
        if (
            page_coverage_ratio is not None
            and page_coverage_ratio < 1.0
            and len(normalized) >= 80
        ):
            # MinerU 的 content_list 可能缺少页码或只记录图片/表格 Block，
            # 但 full.md 仍然包含完整正文。页级 Block 覆盖不足本身只能是
            # 诊断警告；只有和本地文本层对照后确认丢失了大段正文，才拒绝。
            if self._substantive_page_content_missing(
                normalized,
                blocks or [],
                reference_blocks or [],
                expected_page_count or page_count,
            ):
                reasons.append("missing_page_content")
            else:
                warnings.append("incomplete_page_block_coverage")
        layout_risk = self._layout_risk(blocks or [])
        score = max(
            0.0,
            1.0
            - (0.35 if "text_too_short" in reasons else 0.0)
            - min(0.4, replacement_ratio * 10)
            - (0.25 if "low_readable_character_ratio" in reasons else 0.0)
            - (0.2 if "too_little_text_per_page" in reasons else 0.0)
            - (0.2 if "page_count_mismatch" in reasons else 0.0)
            - (0.2 if "excessive_duplicate_lines" in reasons else 0.0),
        )
        return ParseQualityReport(
            accepted=not reasons,
            score=round(score, 4),
            reasons=reasons,
            character_count=len(normalized),
            readable_ratio=round(readable_ratio, 4),
            replacement_ratio=round(replacement_ratio, 4),
            control_ratio=round(control_ratio, 4),
            duplicate_line_ratio=round(duplicate_line_ratio, 4),
            page_coverage_ratio=(
                round(page_coverage_ratio, 4)
                if page_coverage_ratio is not None
                else None
            ),
            layout_risk=layout_risk,
            warnings=warnings,
        )

    @staticmethod
    def _duplicate_line_ratio(text: str) -> float:
        lines = [
            re.sub(r"\s+", " ", line).strip().lower()
            for line in text.splitlines()
            if len(re.sub(r"\s+", "", line)) >= 6
        ]
        if len(lines) < 3:
            return 0.0
        counts = Counter(lines)
        duplicates = sum(count - 1 for count in counts.values() if count > 1)
        return duplicates / len(lines)

    @staticmethod
    def _page_coverage(
        blocks: list[dict[str, Any]], page_count: int | None
    ) -> float | None:
        if not page_count:
            return None
        referenced = {
            int(item["page"])
            for item in blocks
            if isinstance(item.get("page"), int)
            and 1 <= int(item["page"]) <= page_count
            and str(item.get("text") or "").strip()
        }
        if not referenced:
            return None
        return len(referenced) / page_count

    @classmethod
    def _substantive_page_content_missing(
        cls,
        candidate_text: str,
        candidate_blocks: list[dict[str, Any]],
        reference_blocks: list[dict[str, Any]],
        page_count: int | None,
    ) -> bool:
        """判断缺页是否代表正文丢失，而不是只缺少 Block 元数据。

        ``reference_blocks`` 通常来自同一 PDF 的 PyMuPDF 文本层。若没有
        参考文本，无法证明 MinerU 丢失了内容，因此只产生警告。即使有
        参考文本，也要求缺失页包含至少 80 个字符且候选正文明显变短，
        避免把图片页、空白页和 full.md 中已有但未标页码的内容误判为失败。
        """
        if not reference_blocks or not page_count:
            return False
        candidate_pages = {
            int(item["page"])
            for item in candidate_blocks
            if isinstance(item.get("page"), int)
            and 1 <= int(item["page"]) <= page_count
            and str(item.get("text") or "").strip()
        }
        reference_by_page: dict[int, str] = {}
        for item in reference_blocks:
            page = item.get("page")
            value = str(item.get("text") or "").strip()
            if (
                isinstance(page, int)
                and 1 <= page <= page_count
                and value
            ):
                reference_by_page[page] = (
                    reference_by_page.get(page, "") + "\n" + value
                ).strip()
        missing_text = sum(
            len(value)
            for page, value in reference_by_page.items()
            if page not in candidate_pages
        )
        total_reference_text = sum(len(value) for value in reference_by_page.values())
        if missing_text < 80 or total_reference_text <= 0:
            return False
        missing_ratio = missing_text / total_reference_text
        # full.md 可能已包含未标页码的缺失页内容；只有正文总量也明显减少
        # 时才视为真实丢失。
        return missing_ratio >= 0.25 and len(candidate_text) < total_reference_text * 0.8

    @staticmethod
    def _layout_risk(blocks: list[dict[str, Any]]) -> bool:
        by_page: dict[int, list[list[float]]] = {}
        for item in blocks:
            page = item.get("page")
            bbox = item.get("bbox")
            if (
                isinstance(page, int)
                and isinstance(bbox, list)
                and len(bbox) == 4
            ):
                try:
                    by_page.setdefault(page, []).append(
                        [float(value) for value in bbox]
                    )
                except (TypeError, ValueError):
                    continue
        for boxes in by_page.values():
            if len(boxes) < 4:
                continue
            centers = sorted((box[0] + box[2]) / 2 for box in boxes)
            median = centers[len(centers) // 2]
            left = [box for box in boxes if (box[0] + box[2]) / 2 < median]
            right = [box for box in boxes if (box[0] + box[2]) / 2 >= median]
            if len(left) < 2 or len(right) < 2:
                continue
            horizontal_gap = min(box[0] for box in right) - max(
                box[2] for box in left
            )
            vertical_overlaps = sum(
                1
                for first in left
                for second in right
                if min(first[3], second[3]) > max(first[1], second[1])
            )
            if horizontal_gap > 20 and vertical_overlaps >= 2:
                return True
        return False
