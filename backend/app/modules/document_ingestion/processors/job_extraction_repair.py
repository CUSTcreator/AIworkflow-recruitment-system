from __future__ import annotations

from dataclasses import asdict
from typing import Any

from recruitment_ai_core.llm import call_json_llm
from recruitment_ai_core.llm.errors import LLMResponseError

from .job_document_processor import ExtractedJob, JobDocumentProcessor


class JobRepairContractError(LLMResponseError):
    """修复响应不符合合同；按模型边界错误进入 Activity 的规则降级。"""


REPAIRABLE_FIELDS = {
    "title",
    "headcount",
    "responsibilities",
    "qualifications",
    "education_requirement",
    "major_requirement",
    "department_name",
}

JOB_REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["repairs", "unresolved_fields"],
    "properties": {
        "repairs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "value", "source_cell_refs"],
                "properties": {
                    "field": {"type": "string", "enum": sorted(REPAIRABLE_FIELDS)},
                    "value": {},
                    "source_cell_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "unresolved_fields": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(REPAIRABLE_FIELDS)},
        },
    },
}


class JobExtractionRepairService:
    """Repairs only fields already marked unresolved by deterministic extraction."""

    def repair(self, job: ExtractedJob) -> dict[str, Any]:
        states = self._states(job)
        pending = [
            "department_name" if field == "department_id" else field
            for field, state in states.items()
            if state.get("origin") == "unresolved"
            and (field in REPAIRABLE_FIELDS or field == "department_id")
        ]
        if not pending:
            return {"mode": "not_required", "job": asdict(job)}
        source_cells = dict(job.metadata.get("source_cells") or {})
        result, trace = call_json_llm(
            workflow_name="job_document_field_repair",
            schema_name="job_document_field_repair_v1",
            json_schema=JOB_REPAIR_SCHEMA,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你只修复招聘Excel中指定的异常字段。department_name 只表示原表部门名称，"
                        "不能选择或编造系统部门。只能依据给出的原始单元格，不得补写、推测或重写正常字段。"
                        "每条 repairs 必须给出至少一个完整且可验证的原始单元格引用；没有明确依据时不要生成 repairs，"
                        "将字段放入 unresolved_fields。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"待修复字段：{pending}\n"
                        f"当前岗位结构：{job.source_text}\n"
                        f"原始单元格：{source_cells}"
                    ),
                },
            ],
        )
        if result is None:
            raise JobRepairContractError("job_document_field_repair_llm_unavailable")
        if not isinstance(result, dict):
            raise JobRepairContractError("job_document_field_repair_response_invalid")
        repaired = self._apply(job, result, source_cells)
        if not repaired:
            raise JobRepairContractError("job_document_field_repair_no_usable_repairs")
        states = self._states(job)
        remaining = {
            "department_name" if field == "department_id" else field
            for field, state in states.items()
            if isinstance(state, dict) and state.get("origin") == "unresolved"
        }
        return {
            "mode": "llm_repair" if not remaining else "llm_repair_partial",
            "trace": trace,
            "accepted_fields": sorted(
                "department_name" if field == "department_id" else field
                for field in repaired
            ),
            "unresolved_fields": sorted(remaining),
            "degraded": bool(remaining),
            "job": asdict(job),
        }

    @staticmethod
    def _states(job: ExtractedJob) -> dict[str, dict[str, Any]]:
        return dict((job.metadata.get("extraction_meta") or {}).get("fields") or {})

    def _apply(self, job: ExtractedJob, result: dict[str, Any], source_cells: dict[str, str]) -> set[str]:
        states = self._states(job)
        pending = {field for field, state in states.items() if state.get("origin") == "unresolved"}
        repaired: set[str] = set()
        for item in result.get("repairs") or []:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            target_field = "department_id" if field == "department_name" else field
            refs = item.get("source_cell_refs")
            if target_field not in pending or field not in REPAIRABLE_FIELDS or not isinstance(refs, list):
                continue
            refs = [str(value) for value in refs if str(value) in source_cells]
            value = self._valid_value(field, item.get("value"))
            if not refs or value is None:
                continue
            if field == "department_name":
                job.department_name = value
            else:
                setattr(job, field, value)
            states[target_field] = {
                **dict(states.get(target_field) or {}),
                "origin": "llm_repair",
                "message": "AI根据原表内容提取，请核对",
                "source_cell_refs": refs,
            }
            repaired.add(target_field)
        for field in pending - repaired:
            states[field]["origin"] = "unresolved"
        job.metadata["extraction_meta"] = {"fields": states}
        if repaired:
            job.source_text = JobDocumentProcessor.build_source_text(
                job.department_name,
                job.title,
                job.responsibilities,
                job.qualifications,
                job.education_requirement,
                job.major_requirement,
            )
            if "major_requirement" in repaired:
                job.metadata["major_source"] = "llm_repair"
                job.metadata["major_source_quote"] = job.major_requirement or ""
        return repaired

    @staticmethod
    def _valid_value(field: str, value: Any) -> Any:
        if field == "headcount":
            if isinstance(value, bool):
                return None
            if isinstance(value, float) and not value.is_integer():
                return None
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if 1 <= parsed <= 100000 else None
        if field in {"responsibilities", "qualifications"}:
            if not isinstance(value, list):
                return None
            items = [
                str(item).strip()[:500]
                for item in value
                if not isinstance(item, (dict, list, tuple, set))
                and str(item).strip()
            ]
            return items[:50] or None
        if value is None:
            return None
        if isinstance(value, (dict, list, tuple, set)):
            return None
        text = str(value).strip()
        maximum = {
            "title": 128,
            "department_name": 255,
            "education_requirement": 255,
            "major_requirement": 500,
        }.get(field, 500)
        return text[:maximum] if text else None
