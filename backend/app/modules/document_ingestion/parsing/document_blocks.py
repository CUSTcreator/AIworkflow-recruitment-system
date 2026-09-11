from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any


DOCUMENT_BLOCKS_SCHEMA_VERSION = "document_blocks_v1"


def blocks_from_mineru(items: list[Any] | None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            continue
        text = _mineru_text(item)
        block_type = str(item.get("type") or item.get("block_type") or "text")
        if not text and block_type not in {"image", "table"}:
            continue
        bbox = _bbox(item.get("bbox") or item.get("box"))
        blocks.append(
            {
                "block_id": f"B_{index:04d}",
                "page": _positive_int(item.get("page_idx"), offset=1),
                "order": index,
                "text": text,
                "block_type": block_type,
                "bbox": bbox,
                "font_size": _number(item.get("font_size")),
                "is_bold": _optional_bool(item.get("is_bold")),
                "heading_level": _positive_int(
                    item.get("heading_level") or item.get("level")
                ),
                "source_parser": "mineru",
            }
        )
    return blocks


def blocks_from_markdown(text: str, *, source_parser: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", raw)
        content = heading.group(2).strip() if heading else raw
        if not content:
            continue
        blocks.append(
            {
                "block_id": f"B_{len(blocks) + 1:04d}",
                "page": None,
                "order": len(blocks) + 1,
                "text": content,
                "block_type": "title" if heading else "text",
                "bbox": None,
                "font_size": None,
                "is_bold": True if heading else None,
                "heading_level": len(heading.group(1)) if heading else None,
                "source_parser": source_parser,
            }
        )
    return blocks


def reconcile_mineru_blocks(
    markdown: str,
    items: list[Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge MinerU's complete Markdown with its layout-aware content list.

    MinerU can return a complete ``full.md`` while omitting a few entries from
    ``content_list``. The content list remains the preferred source because it
    carries page and bounding-box data; unmatched Markdown paragraphs are
    added as metadata-free blocks so downstream structuring never silently
    loses text merely because one layout item was malformed or absent.
    """

    mineru_blocks = blocks_from_mineru(items)
    discarded_content_items = max(0, len(items or []) - len(mineru_blocks))
    markdown_blocks = [
        block
        for block in blocks_from_markdown(markdown, source_parser="mineru_markdown")
        if not _markdown_decoration_only(str(block.get("text") or ""))
    ]
    if not mineru_blocks:
        return markdown_blocks, {
            "mineru_layout_block_count": 0,
            "markdown_block_count": len(markdown_blocks),
            "supplemented_block_count": len(markdown_blocks),
            "layout_only_block_count": 0,
            "discarded_content_item_count": discarded_content_items,
            "layout_block_match_ratio": 0.0,
        }
    if not markdown_blocks:
        return mineru_blocks, {
            "mineru_layout_block_count": len(mineru_blocks),
            "markdown_block_count": 0,
            "supplemented_block_count": 0,
            "layout_only_block_count": len(mineru_blocks),
            "discarded_content_item_count": discarded_content_items,
            "layout_block_match_ratio": 1.0,
        }

    matches = [_best_layout_match(block, mineru_blocks) for block in markdown_blocks]
    merged: list[dict[str, Any]] = []
    emitted_layout_indexes: set[int] = set()
    supplemented = 0
    matched_character_count = 0
    markdown_character_count = 0

    for markdown_index, markdown_block in enumerate(markdown_blocks):
        comparable = _comparable_block_text(str(markdown_block.get("text") or ""))
        markdown_character_count += len(comparable)
        layout_index = matches[markdown_index]
        if layout_index is not None:
            matched_character_count += len(comparable)
            if layout_index not in emitted_layout_indexes:
                merged.append(dict(mineru_blocks[layout_index]))
                emitted_layout_indexes.add(layout_index)
            continue

        supplement = dict(markdown_block)
        supplement["source_parser"] = "mineru_markdown_supplement"
        supplement["page"] = _nearest_matched_page(
            markdown_index,
            matches,
            mineru_blocks,
        )
        merged.append(supplement)
        supplemented += 1

    # Layout-only image/table blocks and any text blocks not represented in
    # full.md remain useful evidence. Keep them instead of treating Markdown
    # as an exclusive replacement source.
    merged.extend(
        dict(block)
        for index, block in enumerate(mineru_blocks)
        if index not in emitted_layout_indexes
    )
    layout_only_count = len(mineru_blocks) - len(emitted_layout_indexes)
    for order, block in enumerate(merged, start=1):
        original_id = str(block.get("block_id") or "")
        if original_id:
            block["source_block_id"] = original_id
        block["block_id"] = f"B_{order:04d}"
        block["order"] = order

    return merged, {
        "mineru_layout_block_count": len(mineru_blocks),
        "markdown_block_count": len(markdown_blocks),
        "supplemented_block_count": supplemented,
        "layout_only_block_count": layout_only_count,
        "discarded_content_item_count": discarded_content_items,
        "warnings": (
            ["mineru_content_list_supplemented_from_full_markdown"]
            if supplemented
            else []
        ),
        "layout_block_match_ratio": round(
            matched_character_count / max(1, markdown_character_count),
            4,
        ),
    }


def blocks_from_pymupdf(document: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for page_index, page in enumerate(document):
        page_dict = page.get_text("dict", sort=True)
        for raw_block in page_dict.get("blocks", []):
            if not isinstance(raw_block, dict):
                continue
            lines = raw_block.get("lines")
            if not isinstance(lines, list):
                continue
            line_texts: list[str] = []
            sizes: list[float] = []
            bold_values: list[bool] = []
            for line in lines:
                if not isinstance(line, dict):
                    continue
                spans = line.get("spans")
                if not isinstance(spans, list):
                    continue
                fragments: list[str] = []
                for span in spans:
                    if not isinstance(span, dict):
                        continue
                    value = str(span.get("text") or "")
                    if value:
                        fragments.append(value)
                    size = _number(span.get("size"))
                    if size is not None:
                        sizes.append(size)
                    font = str(span.get("font") or "").lower()
                    flags = int(span.get("flags") or 0)
                    bold_values.append("bold" in font or bool(flags & 16))
                line_text = "".join(fragments).strip()
                if line_text:
                    line_texts.append(line_text)
            text = "\n".join(line_texts).strip()
            if not text:
                continue
            blocks.append(
                {
                    "block_id": f"B_{len(blocks) + 1:04d}",
                    "page": page_index + 1,
                    "order": len(blocks) + 1,
                    "text": text,
                    "block_type": "text",
                    "bbox": _bbox(raw_block.get("bbox")),
                    "font_size": max(sizes) if sizes else None,
                    "is_bold": all(bold_values) if bold_values else None,
                    "heading_level": None,
                    "source_parser": "local_pymupdf",
                }
            )
    return blocks


def normalized_block_artifact(
    blocks: list[dict[str, Any]],
    *,
    parser: str,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": DOCUMENT_BLOCKS_SCHEMA_VERSION,
        "source_parser": parser,
        "source_sha256": source_sha256,
        "blocks": sorted(blocks, key=lambda item: int(item.get("order") or 0)),
    }


def block_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(item.get("text") or "").strip()
        for item in sorted(blocks, key=lambda value: int(value.get("order") or 0))
        if str(item.get("text") or "").strip()
    )


def _mineru_text(item: dict[str, Any]) -> str:
    for key in ("text", "content", "markdown", "html"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    table_body = item.get("table_body")
    if isinstance(table_body, str):
        return table_body.strip()
    return ""


def _best_layout_match(
    markdown_block: dict[str, Any],
    layout_blocks: list[dict[str, Any]],
) -> int | None:
    expected = _comparable_block_text(str(markdown_block.get("text") or ""))
    if not expected:
        return None
    best_index: int | None = None
    best_score = 0.0
    for index, block in enumerate(layout_blocks):
        actual = _comparable_block_text(str(block.get("text") or ""))
        if not actual:
            continue
        shorter, longer = sorted((expected, actual), key=len)
        containment = len(shorter) / max(1, len(longer)) if shorter in longer else 0.0
        score = max(containment, SequenceMatcher(None, expected, actual).ratio())
        if score > best_score:
            best_index = index
            best_score = score
    minimum_score = 0.96 if len(expected) < 8 else 0.86
    return best_index if best_score >= minimum_score else None


def _nearest_matched_page(
    markdown_index: int,
    matches: list[int | None],
    layout_blocks: list[dict[str, Any]],
) -> int | None:
    previous_page = _matched_page(
        range(markdown_index - 1, -1, -1), matches, layout_blocks
    )
    next_page = _matched_page(
        range(markdown_index + 1, len(matches)), matches, layout_blocks
    )
    if previous_page is not None and next_page is not None:
        return previous_page if previous_page == next_page else None
    return previous_page if previous_page is not None else next_page


def _matched_page(
    indexes: range,
    matches: list[int | None],
    layout_blocks: list[dict[str, Any]],
) -> int | None:
    for index in indexes:
        layout_index = matches[index]
        if layout_index is None:
            continue
        page = layout_blocks[layout_index].get("page")
        if isinstance(page, int) and page > 0:
            return page
    return None


def _comparable_block_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9@.+%_-]", value.lower()))


def _markdown_decoration_only(value: str) -> bool:
    stripped = value.strip()
    return bool(
        re.fullmatch(r"!\[[^\]]*\]\([^)]*\)", stripped)
        or re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", stripped)
    )


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any, *, offset: int = 0) -> int | None:
    try:
        parsed = int(value) + offset
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_bool(value: Any) -> bool | None:
    return bool(value) if value is not None else None
