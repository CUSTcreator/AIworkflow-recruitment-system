from __future__ import annotations

from typing import Any

from recruitment_ai_core.llm import call_json_llm
from recruitment_ai_core.llm.errors import LLMResponseError

from backend.app.modules.document_ingestion.parsing import ExcelWorkbook

from .job_document_processor import HEADER_ALIASES, JobDocumentProcessor

HEADER_REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sheets"],
    "properties": {
        "sheets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sheet", "header_row_index", "field_columns"],
                "properties": {
                    "sheet": {"type": "string"},
                    "header_row_index": {"type": "integer", "minimum": 0, "maximum": 19},
                    "field_columns": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title"],
                        "properties": {
                            field: {"type": "integer", "minimum": 0}
                            for field in (
                                "sequence",
                                "department",
                                "title",
                                "headcount",
                                "responsibilities",
                                "qualifications",
                                "education",
                                "major",
                            )
                        },
                    },
                },
            },
        },
    },
}


class HeaderRepairContractError(LLMResponseError):
    """表头修复响应不符合合同；交由 Activity 的阻塞/降级策略处理。"""


class JobHeaderRepairService:
    """LLM fallback for an unrecognised header only; row values remain rule parsed."""

    def repair(self, workbook: ExcelWorkbook) -> dict[str, Any]:
        preview = [{"sheet": sheet.name, "rows": sheet.rows[:20]} for sheet in workbook.sheets]
        result, trace = call_json_llm(
            workflow_name="job_document_header_repair",
            schema_name="job_document_header_repair_v1",
            json_schema=HEADER_REPAIR_SCHEMA,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "识别招聘Excel的表头所在行和各字段列号。只允许返回这些字段："
                        "sequence、department、title、headcount、responsibilities、qualifications、education、major。"
                        "每个有效修复计划必须包含 title 列；不能确定的字段不要返回。"
                        "不得解析或生成任何岗位内容。"
                    ),
                },
                {"role": "user", "content": f"工作表前20行：{preview}"},
            ],
        )
        if result is None:
            raise HeaderRepairContractError("job_document_header_repair_llm_unavailable")
        if not isinstance(result, dict):
            raise HeaderRepairContractError("job_document_header_repair_response_invalid")
        repaired: list[str] = []
        repair_plans: list[dict[str, Any]] = []
        sheets = {sheet.name: sheet for sheet in workbook.sheets}
        seen_sheets: set[str] = set()
        for item in result.get("sheets") or []:
            if not isinstance(item, dict):
                continue
            sheet = sheets.get(str(item.get("sheet") or ""))
            row_index = item.get("header_row_index")
            columns = item.get("field_columns")
            if sheet is None or isinstance(row_index, bool) or not isinstance(row_index, int) or not isinstance(columns, dict):
                continue
            if not (0 <= row_index < min(20, len(sheet.rows))):
                continue
            used: set[int] = set()
            accepted: list[tuple[str, int]] = []
            for field, column in columns.items():
                if field not in HEADER_ALIASES or isinstance(column, bool) or not isinstance(column, int):
                    continue
                if column in used or not (0 <= column < len(sheet.rows[row_index])):
                    continue
                used.add(column)
                accepted.append((field, column))
            if not any(field == "title" for field, _ in accepted):
                continue
            if sheet.name in seen_sheets:
                continue
            plan = {"sheet": sheet.name, "header_row_index": row_index, "field_columns": dict(accepted)}
            self.apply_repair_plans(workbook, [plan])
            repair_plans.append(plan)
            repaired.append(sheet.name)
            seen_sheets.add(sheet.name)
        if not repair_plans:
            raise HeaderRepairContractError("job_document_header_repair_no_usable_plan")
        return {"mode": "llm_header_repair", "trace": trace, "repaired_sheets": repaired, "repair_plans": repair_plans}

    @staticmethod
    def apply_repair_plans(workbook: ExcelWorkbook, plans: list[dict[str, Any]]) -> None:
        """把已持久化的表头修复计划重新应用到新读取的工作簿，不再次调用 LLM。"""
        sheets = {sheet.name: sheet for sheet in workbook.sheets}
        for plan in plans:
            sheet = sheets.get(str(plan.get("sheet") or ""))
            row_index = plan.get("header_row_index")
            columns = plan.get("field_columns")
            if sheet is None or isinstance(row_index, bool) or not isinstance(row_index, int) or not isinstance(columns, dict):
                continue
            if not (0 <= row_index < len(sheet.rows)):
                continue
            for field, column in columns.items():
                if field in HEADER_ALIASES and not isinstance(column, bool) and isinstance(column, int) and 0 <= column < len(sheet.rows[row_index]):
                    sheet.rows[row_index][column] = HEADER_ALIASES[field][0]
