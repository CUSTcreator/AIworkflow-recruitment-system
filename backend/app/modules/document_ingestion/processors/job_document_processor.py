from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from difflib import SequenceMatcher

from backend.app.modules.document_ingestion.parsing import ExcelWorkbook


JOB_FIELDS = ("title", "headcount", "responsibilities", "qualifications", "education_requirement", "major_requirement", "department_id")
HEADER_ALIASES = {
    "sequence": ("序号", "编号", "岗位序号"),
    "department": ("部门", "部门名称", "所属部门", "用人部门", "需求部门", "招聘部门"),
    "title": ("职位名称", "岗位名称", "招聘岗位", "职位", "岗位"),
    "headcount": ("招聘人数", "需求人数", "计划人数", "人数", "编制"),
    "responsibilities": ("工作职责", "岗位职责", "职位职责", "工程职责", "职责描述"),
    "qualifications": ("任职资格", "任职要求", "岗位要求", "职位要求", "资格要求"),
    "education": ("学历要求", "最低学历", "学历"),
    "major": ("专业要求", "相关专业", "所学专业", "专业方向", "专业类别"),
}


@dataclass(slots=True)
class ExtractedJob:
    department_name: str
    title: str
    headcount: int | None
    responsibilities: list[str]
    qualifications: list[str]
    education_requirement: str | None
    major_requirement: str | None
    source_text: str
    metadata: dict[str, Any]


class JobDocumentProcessor:
    """Rule-first extraction.  Uncertain fields are preserved for later repair."""

    def extract(self, workbook: ExcelWorkbook) -> tuple[list[ExtractedJob], dict[str, Any]]:
        jobs: list[ExtractedJob] = []
        sheets: list[dict[str, Any]] = []
        header_found = False
        for sheet in workbook.sheets:
            try:
                header_row, columns, header_meta = self._find_header(sheet.rows)
            except RuntimeError:
                sheets.append({"sheet": sheet.name, "status": "skipped", "reason": "header_not_found", "job_count": 0})
                continue
            header_found = True
            last_department = ""
            count = 0
            for row_no, row in enumerate(sheet.rows[header_row + 1:], start=header_row + 2):
                # Excel 单元格常因版面宽度插入软换行；职位、部门等单值字段必须
                # 拼回一行，不能把“设计\n工程师”展示成两段。
                title = self._inline_text(self._value(row, columns.get("title")))
                department = self._inline_text(self._value(row, columns.get("department"))) or last_department
                if self._value(row, columns.get("department")):
                    last_department = department
                responsibilities = self._items(self._value(row, columns.get("responsibilities")))
                qualifications = self._items(self._value(row, columns.get("qualifications")))
                row_cells = self._source_cells(sheet.name, row_no, row)
                if not title and not any((department, responsibilities, qualifications)):
                    continue
                education = self._inline_text(self._value(row, columns.get("education"))) or None
                major = self._inline_text(self._value(row, columns.get("major"))) or None
                major_requirement = major or self._major_from_qualifications(qualifications)
                headcount = self._headcount(self._value(row, columns.get("headcount")))
                field_states = self._field_states(
                    sheet.name, row_no, row, columns, title=title, department=department,
                    responsibilities=responsibilities, qualifications=qualifications,
                    education=education, major=major_requirement, headcount=headcount,
                )
                display_title = title or f"待确认岗位 {row_no}"
                jobs.append(ExtractedJob(
                    department_name=department,
                    title=display_title,
                    headcount=headcount,
                    responsibilities=responsibilities,
                    qualifications=qualifications,
                    education_requirement=education,
                    major_requirement=major_requirement,
                    source_text=self.build_source_text(
                        department,
                        display_title,
                        responsibilities,
                        qualifications,
                        education,
                        major_requirement,
                    ),
                    metadata={
                        "source_sheet": sheet.name,
                        "source_row": row_no,
                        "source_sequence": self._value(row, columns.get("sequence")),
                        "major_source": "column" if major else "qualification_heuristic" if major_requirement else "missing",
                        "major_source_quote": major_requirement or "",
                        "source_cells": row_cells,
                        "header_metadata": header_meta,
                        "extraction_meta": {"fields": field_states},
                    },
                ))
                count += 1
            sheets.append({"sheet": sheet.name, "header_row": header_row + 1, "job_count": count, "header": header_meta})
        if not jobs:
            raise RuntimeError("job_spreadsheet_contains_no_jobs" if header_found else "job_spreadsheet_header_not_found")
        return jobs, {**workbook.metadata, "mode": "deterministic_excel", "job_count": len(jobs), "sheets": sheets}

    @classmethod
    def _find_header(cls, rows: list[list[Any]]) -> tuple[int, dict[str, int], dict[str, Any]]:
        best: tuple[int, dict[str, int], dict[str, Any]] | None = None
        for index, row in enumerate(rows[:20]):
            columns, ambiguous, candidates = cls._assign_headers(row)
            if "title" in columns:
                meta = {
                    "match_mode": "global_one_to_one",
                    "matched_fields": sorted(columns),
                    "ambiguous_fields": sorted(ambiguous),
                    "missing_fields": [name for name in HEADER_ALIASES if name not in columns],
                    "candidate_fields": candidates,
                }
                if best is None or len(columns) > len(best[1]):
                    best = (index, columns, meta)
        if best is None:
            raise RuntimeError("job_spreadsheet_header_not_found")
        return best

    @classmethod
    def _header_matches(cls, value: Any) -> list[str]:
        name = cls._normalize_header(value)
        if not name:
            return []
        exact = [field for field, aliases in HEADER_ALIASES.items() if name in {cls._normalize_header(alias) for alias in aliases}]
        if exact:
            return exact
        partial = [field for field, aliases in HEADER_ALIASES.items() if any(
            len(alias) >= 3 and (alias in name or name in alias)
            for alias in {cls._normalize_header(a) for a in aliases}
        )]
        return partial

    @classmethod
    def _header_score(cls, value: Any, field: str) -> int:
        """Return a conservative similarity score for one header/field pair."""
        name = cls._normalize_header(value)
        aliases = [cls._normalize_header(alias) for alias in HEADER_ALIASES[field]]
        if not name:
            return 0
        if name in aliases:
            return 100
        if any(len(alias) >= 3 and (alias in name or name in alias) for alias in aliases):
            return 80
        ratio = max((SequenceMatcher(None, name, alias).ratio() for alias in aliases), default=0.0)
        return 60 if ratio >= 0.72 else 0

    @classmethod
    def _assign_headers(cls, row: list[Any]) -> tuple[dict[str, int], set[str], dict[str, list[dict[str, Any]]]]:
        """Globally assign headers one-to-one so local greedy matches cannot hide columns."""
        pair_scores: dict[str, list[tuple[int, int]]] = {}
        candidate_view: dict[str, list[dict[str, Any]]] = {}
        for col, cell in enumerate(row):
            candidates = [(field, cls._header_score(cell, field)) for field in HEADER_ALIASES]
            candidates = [(field, score) for field, score in candidates if score > 0]
            if candidates:
                for field, score in candidates:
                    pair_scores.setdefault(field, []).append((col, score))
                candidate_view[str(col)] = [
                    {"field": field, "score": score} for field, score in sorted(candidates, key=lambda item: (-item[1], item[0]))
                ]

        ambiguous: set[str] = set()
        usable: dict[str, list[tuple[int, int]]] = {}
        for field, options in pair_scores.items():
            highest = max(score for _, score in options)
            top = [(col, score) for col, score in options if highest - score <= 3]
            if len(top) > 1:
                ambiguous.add(field)
            else:
                # Excel 表头通常很短；限制低分候选，避免异常模板导致组合搜索爆炸，
                # 同时保留足够的近似列供全局冲突消解。
                usable[field] = sorted(
                    [item for item in options if highest - item[1] <= 20],
                    key=lambda item: (-item[1], item[0]),
                )[:6]

        fields = sorted(usable, key=lambda field: (len(usable[field]), field))
        best_score = -1
        best: dict[str, int] = {}

        def visit(position: int, used: set[int], score: int, assigned: dict[str, int]) -> None:
            nonlocal best_score, best
            if position >= len(fields):
                if score > best_score or (score == best_score and len(assigned) > len(best)):
                    best_score, best = score, dict(assigned)
                return
            field = fields[position]
            visit(position + 1, used, score, assigned)
            for col, pair_score in sorted(usable[field], key=lambda item: -item[1]):
                if col in used:
                    continue
                assigned[field] = col
                visit(position + 1, used | {col}, score + pair_score, assigned)
                assigned.pop(field, None)

        visit(0, set(), 0, {})
        return best, ambiguous, candidate_view

    @classmethod
    def _field_states(cls, sheet: str, row_no: int, row: list[Any], columns: dict[str, int], *, title: str, department: str, responsibilities: list[str], qualifications: list[str], education: str | None, major: str | None, headcount: int | None) -> dict[str, dict[str, Any]]:
        source_name = {"education_requirement": "education", "major_requirement": "major", "department_id": "department"}
        def item(field: str, origin: str = "rule", message: str = "") -> dict[str, Any]:
            column = columns.get(source_name.get(field, field))
            return {
                "origin": origin,
                "message": message,
                "source_cell_refs": [cls._cell_ref(sheet, row_no, column)] if column is not None else [],
            }
        states = {field: item(field) for field in JOB_FIELDS}
        parsed = {
            "title": title,
            "department_id": department,
            "headcount": headcount,
            "responsibilities": responsibilities,
            "qualifications": qualifications,
            "education_requirement": education,
            "major_requirement": major,
        }
        for field, state in states.items():
            source_field = source_name.get(field, field)
            column = columns.get(source_field)
            raw = cls._value(row, column)
            # 将“没有来源列”和“来源单元格为空”写入溯源元数据，供读模型和恢复页
            # 区分合法空值与确实丢失的解析结果；两者不能共用 unresolved 状态。
            state["source_column_exists"] = column is not None
            state["source_cell_non_empty"] = bool(raw)
        # 空单元格或缺少可选列是合法输入；只有有原文却被规则解析丢失时才请求 LLM 修复。
        for field, value in parsed.items():
            source_field = source_name.get(field, field)
            column = columns.get(source_field)
            if column is None:
                continue
            raw = cls._value(row, column)
            if raw and cls._suspicious_loss(field, raw, value):
                states[field].update(item(field, "unresolved", "原表有内容但解析结果疑似丢失，请确认"))
        return states

    @staticmethod
    def _suspicious_loss(field: str, raw: str, parsed: Any) -> bool:
        if not raw:
            return False
        if parsed is None or parsed == "" or parsed == []:
            return True
        if field == "headcount":
            return False
        def compact(value: Any) -> str:
            return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).casefold()
        source = compact(raw)
        target = compact("".join(parsed) if isinstance(parsed, list) else parsed)
        if not source or not target:
            return bool(source)
        # 去编号、空格、换行和标点属于正常规范化；仅在主体覆盖率明显不足时修复。
        return len(target) / len(source) < 0.35

    @staticmethod
    def _normalize_header(value: Any) -> str:
        return re.sub(r"[\s:：()（）/；;_-]+", "", str(value or "")).casefold()

    @staticmethod
    def _value(row: list[Any], column: int | None) -> str:
        if column is None or column >= len(row) or row[column] is None:
            return ""
        value = row[column]
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()

    @staticmethod
    def _headcount(value: str) -> int | None:
        match = re.search(r"\d+", value)
        return int(match.group()) if match and int(match.group()) > 0 else None

    @staticmethod
    def _inline_text(value: str) -> str:
        """合并单值单元格的排版软换行，不用于职责/资格等多条目字段。"""
        return re.sub(r"\s*\n\s*", "", value).strip()

    @staticmethod
    def _items(value: str) -> list[str]:
        """按显式条目标记分条，并合并没有句末标点的 Excel 软换行。"""
        if not value:
            return []
        lines = [line.strip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
        marker = re.compile(
            r"^\s*(?:(?:\d+|[一二三四五六七八九十]+)[.．、）)]|[（(]\d+[）)]|[•·●▪])\s*(.*)$"
        )
        items: list[str] = []
        current: str | None = None
        numbered = False
        for line in lines:
            matched = marker.match(line)
            if matched:
                numbered = True
                if current:
                    items.append(current)
                current = matched.group(1).strip()
            elif current is not None:
                # 没有编号的新行只是单元格内折行，不能被误认为下一条资格。
                current += line
            else:
                items.append(line)
        if current:
            items.append(current)
        if numbered:
            return items

        # 无编号时，换行既可能是真实分条，也可能只是单元格显示折行。上一行没有
        # 句末标点且下一行不像新要求开头时，保守地拼回原句；其他换行继续分条。
        joined: list[str] = []
        new_item = re.compile(
            r"^(?:负责|参与|承担|完成|熟悉|掌握|具备|通过|持有|取得|能够|善于|"
            r"身体|热爱|研究方向|专业|有过|具有)"
        )
        for line in lines:
            if (
                joined
                and not re.search(r"[。；;！？!?：:]$", joined[-1])
                and not new_item.search(line)
            ):
                joined[-1] += line
            else:
                joined.append(line)
        return joined

    @staticmethod
    def _major_from_qualifications(items: list[str]) -> str | None:
        """只从明确的“专业名单”条目提取，避免把技能、知识等泛化资格混入专业要求。"""
        for item in items:
            text = item.strip()
            # “仪器科学与技术、控制工程及其相关专业”“机械工程等专业”属于名单表达；
            # “掌握相关专业技术知识”等能力描述虽然包含“专业”，但不属于专业要求。
            is_named_major_list = bool(re.search(r"(?:及其|等|相关)专业(?:[；。]|$)", text))
            is_explicit_field = bool(re.match(r"^(?:所学|相关)?专业\s*[：:]", text))
            is_generic_knowledge = bool(re.search(r"(?:掌握|熟悉|了解|具备|基础知识|专业知识|学习成绩)", text))
            if (is_named_major_list or is_explicit_field) and not is_generic_knowledge:
                return text[:500]
        return None

    @classmethod
    def _source_cells(cls, sheet: str, row: int, values: list[Any]) -> dict[str, str]:
        return {cls._cell_ref(sheet, row, col): cls._value(values, col) for col in range(len(values)) if cls._value(values, col)}

    @staticmethod
    def _cell_ref(sheet: str, row: int, col: int) -> str:
        col += 1
        letters = ""
        while col:
            col, remainder = divmod(col - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{sheet}!{letters}{row}"

    @staticmethod
    def build_source_text(
        department: str,
        title: str,
        responsibilities: list[str],
        qualifications: list[str],
        education: str | None,
        major: str | None,
    ) -> str:
        lines = [
            f"部门：{department}" if department else "", f"职位名称：{title}",
            "工作职责：", *[f"{index}. {value}" for index, value in enumerate(responsibilities, 1)],
            "任职资格：", *[f"{index}. {value}" for index, value in enumerate(qualifications, 1)],
            f"学历要求：{education}" if education else "", f"专业要求：{major}" if major else "",
        ]
        return "\n".join(item for item in lines if item)

    @staticmethod
    def _source_text(
        department: str,
        title: str,
        responsibilities: list[str],
        qualifications: list[str],
        education: str | None,
        major: str | None,
    ) -> str:
        """Compatibility wrapper for historical callers."""
        return JobDocumentProcessor.build_source_text(
            department,
            title,
            responsibilities,
            qualifications,
            education,
            major,
        )
