from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ParseQualityReport:
    accepted: bool
    score: float
    reasons: list[str]
    character_count: int
    readable_ratio: float
    replacement_ratio: float
    control_ratio: float
    duplicate_line_ratio: float
    page_coverage_ratio: float | None = None
    layout_risk: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParseCandidate:
    provider: str
    text: str
    blocks: list[dict[str, Any]]
    page_count: int | None
    metadata: dict[str, Any] = field(default_factory=dict)
    quality: ParseQualityReport | None = None


@dataclass(slots=True)
class ParseComparison:
    consistent: bool
    content_recall: float
    reverse_content_recall: float
    length_ratio: float
    critical_token_recall: float
    numeric_token_recall: float
    reading_order_risk: bool
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParseDecision:
    selected_provider: str
    reasons: list[str]
    vlm_triggered: bool = False
    vlm_trigger_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
