from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Traceable verbatim quote from a source object."""

    source_id: str
    quote: str
    source_type: str | None = None
    location: str | None = None

    def as_dict(self) -> dict[str, str]:
        value = {"source_id": self.source_id, "quote": self.quote}
        if self.source_type:
            value["source_type"] = self.source_type
        if self.location:
            value["location"] = self.location
        return value


class SourceRefError(ValueError):
    pass


def validate_source_ref(value: SourceRef | Mapping[str, Any], *, source_text: str | None = None) -> SourceRef:
    if isinstance(value, SourceRef):
        ref = value
    else:
        source_id = str(value.get("source_id") or value.get("segment_id") or value.get("bullet_id") or "").strip()
        quote = str(value.get("quote") or "").strip()
        ref = SourceRef(
            source_id=source_id,
            quote=quote,
            source_type=_optional_text(value.get("source_type")),
            location=_optional_text(value.get("location")),
        )
    if not ref.source_id:
        raise SourceRefError("source_ref_source_id_missing")
    if not ref.quote:
        raise SourceRefError("source_ref_quote_missing")
    if "..." in ref.quote or "……" in ref.quote:
        raise SourceRefError("source_ref_quote_contains_ellipsis")
    if source_text is not None and ref.quote not in source_text:
        raise SourceRefError("source_ref_quote_not_verbatim")
    return ref


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
