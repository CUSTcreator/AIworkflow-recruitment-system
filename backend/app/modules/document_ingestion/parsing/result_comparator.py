from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .contracts import ParseCandidate, ParseComparison


class ParseResultComparator:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        options = config or {}
        self.content_recall_threshold = float(
            options.get("content_recall_threshold", 0.92)
        )
        self.reverse_recall_threshold = float(
            options.get("reverse_content_recall_threshold", 0.85)
        )
        self.critical_token_threshold = float(
            options.get("critical_token_recall_threshold", 0.98)
        )
        self.numeric_token_threshold = float(
            options.get("numeric_token_recall_threshold", 0.98)
        )
        self.minimum_length_ratio = float(
            options.get("minimum_length_ratio", 0.75)
        )
        self.maximum_length_ratio = float(
            options.get("maximum_length_ratio", 1.30)
        )
        # 上面的阈值用于记录“文本有差异”的软警告；只有达到更严重的
        # 丢失比例或长度差异，才会把结果判为硬冲突并触发视觉兜底。
        self.hard_content_recall_threshold = float(
            options.get("hard_content_recall_threshold", 0.72)
        )
        self.hard_reverse_recall_threshold = float(
            options.get("hard_reverse_content_recall_threshold", 0.60)
        )
        self.hard_minimum_length_ratio = float(
            options.get("hard_minimum_length_ratio", 0.45)
        )
        self.hard_maximum_length_ratio = float(
            options.get("hard_maximum_length_ratio", 2.50)
        )

    def compare(
        self,
        primary: ParseCandidate,
        text_layer: ParseCandidate,
    ) -> ParseComparison:
        primary_text = _comparable_text(primary.text)
        anchor_text = _comparable_text(text_layer.text)
        content_recall = _ngram_recall(primary_text, anchor_text)
        reverse_recall = _ngram_recall(anchor_text, primary_text)
        length_ratio = len(primary_text) / max(1, len(anchor_text))
        critical_recall = _token_recall(
            _critical_tokens(primary.text),
            _critical_tokens(text_layer.text),
        )
        numeric_recall = _token_recall(
            _numeric_tokens(primary.text),
            _numeric_tokens(text_layer.text),
        )
        reading_order_risk = bool(
            (primary.quality and primary.quality.layout_risk)
            or (text_layer.quality and text_layer.quality.layout_risk)
        )
        reasons: list[str] = []
        warnings: list[str] = []
        if content_recall < self.content_recall_threshold:
            warnings.append("content_recall_below_threshold")
        if reverse_recall < self.reverse_recall_threshold:
            warnings.append("reverse_content_recall_below_threshold")
        if not self.minimum_length_ratio <= length_ratio <= self.maximum_length_ratio:
            warnings.append("text_length_ratio_out_of_range")
        # 仅当一侧明显缺失且总体长度也显著偏短/偏长时，才认为是正文
        # 丢失。换行、标题格式和阅读顺序变化通常只会落入 warnings。
        if (
            (
                content_recall < self.hard_content_recall_threshold
                and reverse_recall < self.hard_reverse_recall_threshold
            )
            or (
                content_recall < self.hard_content_recall_threshold
                and length_ratio < self.minimum_length_ratio
            )
        ) or (
            reverse_recall < self.hard_reverse_recall_threshold
            and length_ratio > self.maximum_length_ratio
        ):
            reasons.append("substantive_content_conflict")
        if not self.hard_minimum_length_ratio <= length_ratio <= self.hard_maximum_length_ratio:
            # 单独的长度差异可能来自 Markdown 表格、标题标记或更细的
            # Block 切分；只有长度极端且同时缺少对方正文时才硬拒绝。
            extreme_length_conflict = (
                length_ratio < self.hard_minimum_length_ratio
                and content_recall < self.hard_content_recall_threshold
            ) or (
                length_ratio > self.hard_maximum_length_ratio
                and reverse_recall < self.hard_reverse_recall_threshold
            )
            if extreme_length_conflict:
                reasons.append("severe_text_length_ratio_conflict")
            else:
                warnings.append("severe_text_length_ratio_out_of_range")
        # Token differences identify where a parser may have made a local OCR
        # mistake; they do not prove that the whole MinerU document is bad.
        # Keep them observable and let page/block repair handle local defects.
        if critical_recall < self.critical_token_threshold:
            warnings.append("critical_token_difference")
        if numeric_recall < self.numeric_token_threshold:
            warnings.append("numeric_token_difference")
        if reading_order_risk:
            warnings.append("reading_order_risk")
        return ParseComparison(
            consistent=not reasons,
            content_recall=round(content_recall, 4),
            reverse_content_recall=round(reverse_recall, 4),
            length_ratio=round(length_ratio, 4),
            critical_token_recall=round(critical_recall, 4),
            numeric_token_recall=round(numeric_recall, 4),
            reading_order_risk=reading_order_risk,
            reasons=reasons,
            warnings=warnings,
        )


def _comparable_text(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"https?://\S+", "", lowered)
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9@.+%_-]", lowered))


def _ngrams(value: str, size: int = 3) -> Counter[str]:
    if not value:
        return Counter()
    if len(value) < size:
        return Counter({value: 1})
    return Counter(value[index : index + size] for index in range(len(value) - size + 1))


def _ngram_recall(candidate: str, reference: str) -> float:
    expected = _ngrams(reference)
    if not expected:
        return 1.0 if not candidate else 0.0
    actual = _ngrams(candidate)
    matched = sum(min(count, actual.get(token, 0)) for token, count in expected.items())
    return matched / sum(expected.values())


def _critical_tokens(value: str) -> list[str]:
    patterns = (
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        r"(?:19|20)\d{2}(?:[./年-]\d{1,2})?",
        r"\d+(?:\.\d+)?%",
        r"[A-Za-z][A-Za-z0-9_.+#/-]{2,}",
    )
    tokens: list[str] = []
    for pattern in patterns:
        tokens.extend(match.lower() for match in re.findall(pattern, value))
    return tokens


def _numeric_tokens(value: str) -> list[str]:
    return [
        token.replace(" ", "")
        for token in re.findall(
            r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?:[./年-]\d{1,2})?|"
            r"(?<![A-Za-z0-9])\d+(?:\.\d+)?%?|"
            r"(?<!\d)1[3-9]\d{9}(?!\d)",
            value,
        )
    ]


def _token_recall(candidate: list[str], reference: list[str]) -> float:
    expected = Counter(reference)
    if not expected:
        return 1.0
    actual = Counter(candidate)
    matched = sum(min(count, actual.get(token, 0)) for token, count in expected.items())
    return matched / sum(expected.values())
