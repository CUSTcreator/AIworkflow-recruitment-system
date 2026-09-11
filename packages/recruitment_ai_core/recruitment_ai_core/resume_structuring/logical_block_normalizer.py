from __future__ import annotations

"""Normalize parser blocks into source-traceable logical resume fields."""

from html.parser import HTMLParser
import re
from typing import Any


_KNOWN_FIELD_LABELS = {
    "姓名", "手机号码", "手机号", "联系电话", "邮箱", "性别", "民族", "年龄",
    "出生日期", "当前职位", "目标岗位", "求职意向", "最高学历", "最高学位",
    "教育部一级学科", "毕业时间", "个人特长/自我评价", "个人特长", "自我评价",
    "个人评价", "项目名称", "项目描述", "项目简介", "项目介绍", "项目概述",
    "项目职责", "项目中职责", "项目角色", "项目成果", "项目时间", "岗位",
    "职位", "职务", "部门", "证明人", "联系人", "实习内容", "工作内容",
    "工作职责", "职务描述", "实践描述", "职责描述", "技术栈", "开发环境",
    "获奖时间", "获奖项", "获奖级别", "获奖描述", "上传证明材料", "名称",
    "发布时间", "作者顺序", "所属期刊", "工作单位", "与本人关系", "本人声明",
    "英语等级", "英语等级成绩", "英语水平", "外语等级", "外语水平", "语言能力",
    "证书名称", "资格证书", "职业资格", "认证资质", "专业技能", "技能清单",
    "技能专长", "核心技能", "技术技能", "软件能力", "工具能力", "技术能力",
    "专业能力", "计算机技能", "软件技能", "熟悉工具",
}

_EMBEDDED_FIELD_LABELS = tuple(sorted(_KNOWN_FIELD_LABELS, key=len, reverse=True))
_COLON_FIELD_MARKER = re.compile(
    rf"(?P<label>{'|'.join(re.escape(item) for item in _EMBEDDED_FIELD_LABELS)})\s*[：:]"
)
_SPACED_FIELD_MARKER = re.compile(
    rf"(?:^|[\s|｜；;。])(?P<label>{'|'.join(re.escape(item) for item in _EMBEDDED_FIELD_LABELS)})\s+(?=\S)"
)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(_clean_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None


def expand_logical_block(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand one physical parser Block into ordered logical fields.

    Logical children may normalize a label/value boundary, but never invent
    content. Every child keeps the immutable parser Block ID for publication and
    correction while its own ID remains stable inside the structuring run.
    """
    text = str(block.get("text") or "")
    is_table = (
        str(block.get("block_type") or "").casefold() == "table"
        or "<table" in text.casefold()
    )
    return _expand_table(block) if is_table else _expand_text(block)


def source_block_id(block: dict[str, Any]) -> str:
    """Return the immutable parser Block behind a logical child."""
    return str(block.get("source_block_id") or block.get("parent_block_id") or block.get("block_id") or "")


def _expand_table(block: dict[str, Any]) -> list[dict[str, Any]]:
    parser = _TableParser()
    try:
        parser.feed(str(block.get("text") or ""))
        parser.close()
    except Exception:
        return _expand_text(block)
    if not parser.rows:
        return _expand_text(block)

    parent_id = str(block.get("block_id") or "")
    output: list[dict[str, Any]] = []
    for row_index, cells in enumerate(parser.rows, start=1):
        fields = [
            part
            for label, value in _logical_fields(cells)
            for part in _split_embedded_fields(label, value)
        ]
        for field_index, (label, value) in enumerate(fields, start=1):
            output.append(
                _logical_child(
                    block,
                    block_id=(
                        f"{parent_id}_R{row_index:03d}_F{field_index:02d}"
                        if parent_id else ""
                    ),
                    block_type="table_field" if label else "table_row",
                    label=label,
                    value=value,
                    bbox=_row_bbox(block.get("bbox"), row_index, len(parser.rows)),
                    table_row_index=row_index,
                    table_field_index=field_index,
                    table_cells=list(cells),
                )
            )
    return output or [dict(block)]


def _expand_text(block: dict[str, Any]) -> list[dict[str, Any]]:
    raw_text = str(block.get("text") or "")
    lines = [(match.start(), match.group()) for match in re.finditer(r"[^\r\n]+", raw_text)]
    if not lines:
        return [dict(block)]
    parent_id = str(block.get("block_id") or "")
    parts: list[tuple[int, int, str | None, str]] = []
    for offset, raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        for label, value, relative_start, relative_end in _split_text_fields(line):
            parts.append((offset + relative_start, offset + relative_end, label, value))
    if len(parts) == 1 and parts[0][2] is None and parts[0][3] == raw_text.strip():
        child = dict(block)
        child["source_block_id"] = parent_id or None
        child["logical_block_id"] = parent_id or None
        return [child]

    output: list[dict[str, Any]] = []
    for index, (start, end, label, value) in enumerate(parts, start=1):
        output.append(
            _logical_child(
                block,
                block_id=f"{parent_id}_L{index:03d}" if parent_id else "",
                block_type="text_field" if label else "text_line",
                label=label,
                value=value,
                bbox=block.get("bbox"),
                source_char_start=start,
                source_char_end=end,
            )
        )
    return output or [dict(block)]


def _split_text_fields(text: str) -> list[tuple[str | None, str, int, int]]:
    markers: dict[int, tuple[int, str]] = {}
    for match in _COLON_FIELD_MARKER.finditer(text):
        markers[match.start("label")] = (match.end(), match.group("label"))
    for match in _SPACED_FIELD_MARKER.finditer(text):
        start = match.start("label")
        markers.setdefault(start, (match.end(), match.group("label")))
    ordered = sorted((start, end, label) for start, (end, label) in markers.items())
    if not ordered:
        return [(None, text, 0, len(text))]

    output: list[tuple[str | None, str, int, int]] = []
    leading = text[: ordered[0][0]].strip(" ，,；;。|｜")
    if leading:
        leading_start = text.find(leading)
        output.append((None, leading, leading_start, leading_start + len(leading)))
    for index, (start, value_start, label) in enumerate(ordered):
        end = ordered[index + 1][0] if index + 1 < len(ordered) else len(text)
        value = text[value_start:end].strip(" ，,；;。|｜")
        if value:
            actual_start = text.find(value, value_start, end)
            output.append((label, value, start, actual_start + len(value)))
        else:
            output.append((label, "", start, value_start))
    return output


def _split_embedded_fields(label: str | None, value: str) -> list[tuple[str | None, str]]:
    split = _split_text_fields(value)
    if len(split) == 1 and split[0][0] is None:
        return [(label, value)]
    output: list[tuple[str | None, str]] = []
    for embedded_label, embedded_value, _start, _end in split:
        output.append((embedded_label or label, embedded_value))
    return output or [(label, value)]


def _logical_fields(cells: list[str]) -> list[tuple[str | None, str]]:
    values = [item for item in cells if item]
    if not values:
        return []
    if len(values) == 1:
        return [(None, values[0])]
    if len(values) % 2 == 0 and any(
        _is_field_label(values[index]) for index in range(0, len(values), 2)
    ):
        return [
            (values[index].rstrip("：:"), values[index + 1])
            for index in range(0, len(values), 2)
        ]
    output: list[tuple[str | None, str]] = []
    index = 0
    while index < len(values):
        current = values[index]
        if _is_field_label(current):
            output.append((current.rstrip("：:"), values[index + 1] if index + 1 < len(values) else ""))
            index += 2
            continue
        output.append((None, " | ".join(values[index:])))
        break
    return output


def _logical_child(
    block: dict[str, Any],
    *,
    block_id: str,
    block_type: str,
    label: str | None,
    value: str,
    bbox: Any,
    **metadata: Any,
) -> dict[str, Any]:
    parent_id = str(block.get("source_block_id") or block.get("block_id") or "")
    child = dict(block)
    child.update(metadata)
    child["parent_block_id"] = parent_id or None
    child["source_block_id"] = parent_id or None
    child["block_id"] = block_id
    child["logical_block_id"] = block_id or None
    child["block_type"] = block_type
    child["field_label"] = label or None
    child["field_value"] = value or None
    child["text"] = _field_text(label, value)
    child["bbox"] = bbox
    child["parent_bbox"] = block.get("bbox")
    return child


def _is_field_label(value: str) -> bool:
    normalized = re.sub(r"[\s：:]+", "", value)
    if normalized in {re.sub(r"[\s：:]+", "", item) for item in _KNOWN_FIELD_LABELS}:
        return True
    return bool(
        len(normalized) <= 12
        and re.fullmatch(
            r"(?:项目|实习|工作|职务|实践|获奖|论文|教育|学历|专业|学校|学院|"
            r"成绩|排名|附件|家庭|附加|简历|语言|英语|外语|证书|资格|认证|资质|个人)[\w\u4e00-\u9fff/]*",
            normalized,
        )
    )


def _field_text(label: str | None, value: str) -> str:
    if label and value:
        return f"{label}：{value}"
    return label or value


def _clean_text(value: str) -> str:
    lines = [re.sub(r"\s+", " ", item).strip() for item in value.splitlines()]
    return "\n".join(item for item in lines if item).strip()


def _row_bbox(value: Any, row_index: int, row_count: int) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4 or row_count <= 0:
        return None
    try:
        left, top, right, bottom = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    height = max(0.0, bottom - top) / row_count
    row_top = top + height * (row_index - 1)
    return [left, row_top, right, row_top + height]
