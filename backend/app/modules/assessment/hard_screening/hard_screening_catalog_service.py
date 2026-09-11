from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import HardScreeningCriterion, User
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessError


VALUE_MODES = {"select", "number", "text"}
BINDINGS = {
    "highest_degree", "highest_education_status",
    "highest_education_graduation_year", "relevant_experience_years",
    "project_experience_semantic", "skills_semantic",
    "certifications_semantic", "full_resume_semantic",
}
BINDING_SPECS: dict[str, tuple[str, str, str]] = {
    "highest_degree": ("select", "education", "degree_at_least"),
    "highest_education_status": ("select", "education", "education_status_is"),
    "highest_education_graduation_year": ("number", "education", "year_between"),
    "relevant_experience_years": ("number", "work_experience", "years_between"),
    "project_experience_semantic": ("text", "project_experience", "semantic_match"),
    "skills_semantic": ("text", "skills", "semantic_match"),
    "certifications_semantic": ("text", "certifications", "semantic_match"),
    "full_resume_semantic": ("text", "full_resume", "semantic_match"),
}
DEFAULTS: list[dict[str, Any]] = [
    {"code": "minimum_degree", "name": "最低学历", "value_mode": "select", "allowed_values": ["大专", "本科", "硕士", "博士"], "evaluation_binding": "highest_degree"},
    {"code": "highest_education_status", "name": "最高学历状态", "value_mode": "select", "allowed_values": ["已毕业", "在读"], "evaluation_binding": "highest_education_status"},
    {"code": "highest_education_graduation_year", "name": "最高学历毕业年份", "value_mode": "number", "allowed_values": [], "evaluation_binding": "highest_education_graduation_year"},
    {"code": "minimum_experience_years", "name": "相关工作经验", "value_mode": "number", "allowed_values": [], "evaluation_binding": "relevant_experience_years"},
    {"code": "project_experience", "name": "相关项目经验", "value_mode": "text", "allowed_values": [], "evaluation_binding": "project_experience_semantic"},
    {"code": "required_skill", "name": "必备技能", "value_mode": "text", "allowed_values": [], "evaluation_binding": "skills_semantic"},
    {"code": "certification", "name": "证书/资质", "value_mode": "text", "allowed_values": [], "evaluation_binding": "certifications_semantic"},
    {"code": "custom", "name": "自定义条件", "value_mode": "text", "allowed_values": [], "evaluation_binding": "full_resume_semantic"},
]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _id() -> str:
    return f"HSC_{uuid.uuid4().hex[:20].upper()}"


def _code(value: str) -> str:
    code = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return code or f"custom_{uuid.uuid4().hex[:10]}"


class HardScreeningCatalogService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_defaults(self) -> None:
        existing = set(self.db.scalars(select(HardScreeningCriterion.code)))
        for index, item in enumerate(DEFAULTS, start=1):
            if item["code"] in existing:
                continue
            self.db.add(HardScreeningCriterion(
                criterion_id=_id(), code=item["code"], name=item["name"],
                value_mode=item["value_mode"], allowed_values_json={"items": item["allowed_values"]},
                evaluation_binding=item["evaluation_binding"], enabled=True, sort_order=index,
                is_builtin=True, created_at=_now(), updated_at=_now(),
            ))
        self.db.flush()

    def list(self, *, include_deleted: bool = False, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = select(HardScreeningCriterion)
        if not include_deleted:
            query = query.where(HardScreeningCriterion.deleted_at.is_(None))
        if enabled_only:
            query = query.where(HardScreeningCriterion.enabled.is_(True))
        rows = self.db.scalars(query.order_by(HardScreeningCriterion.sort_order, HardScreeningCriterion.created_at)).all()
        return [self._view(row) for row in rows]

    def get_active_map(self) -> dict[str, dict[str, Any]]:
        return {item["criterionId"]: item for item in self.list(enabled_only=True)}

    def create(self, actor: User, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_defaults()
        name = str(data.get("name") or "").strip()
        if not name:
            raise BusinessError("hard_screening_criterion_name_missing", "硬筛选项名称不能为空")
        mode = str(data.get("value_mode") or data.get("valueMode") or "text")
        binding = str(data.get("evaluation_binding") or data.get("evaluationBinding") or "full_resume_semantic")
        self._validate(mode, binding, data.get("allowed_values") or data.get("allowedValues") or [])
        base_code = _code(str(data.get("code") or name))
        code = base_code
        counter = 2
        while self.db.scalar(select(HardScreeningCriterion).where(HardScreeningCriterion.code == code)) is not None:
            code = f"{base_code}_{counter}"; counter += 1
        row = HardScreeningCriterion(
            criterion_id=_id(), code=code, name=name, value_mode=mode,
            allowed_values_json={"items": self._values(data.get("allowed_values") or data.get("allowedValues") or [])},
            evaluation_binding=binding, enabled=bool(data.get("enabled", True)),
            sort_order=int(data.get("sort_order") or data.get("sortOrder") or 999), is_builtin=False,
            created_at=_now(), updated_at=_now(),
        )
        self.db.add(row); self.db.flush(); self._audit(actor, "create", row); self.db.flush()
        return self._view(row)

    def update(self, actor: User, criterion_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.ensure_defaults()
        row = self.db.get(HardScreeningCriterion, criterion_id)
        if row is None or row.deleted_at is not None:
            raise BusinessError("hard_screening_criterion_not_found", "未找到硬筛选项", status_code=404)
        name = str(data.get("name", row.name)).strip()
        mode = str(data.get("value_mode", data.get("valueMode", row.value_mode)))
        binding = str(data.get("evaluation_binding", data.get("evaluationBinding", row.evaluation_binding)))
        values = data.get("allowed_values", data.get("allowedValues", (row.allowed_values_json or {}).get("items") or []))
        self._validate(mode, binding, values)
        row.name = name; row.value_mode = mode; row.evaluation_binding = binding
        row.allowed_values_json = {"items": self._values(values)}
        row.enabled = bool(data.get("enabled", row.enabled)); row.sort_order = int(data.get("sort_order", data.get("sortOrder", row.sort_order)))
        row.updated_at = _now(); self.db.flush(); self._audit(actor, "update", row); self.db.flush()
        return self._view(row)

    def delete(self, actor: User, criterion_id: str) -> None:
        self.ensure_defaults(); row = self.db.get(HardScreeningCriterion, criterion_id)
        if row is None or row.deleted_at is not None:
            raise BusinessError("hard_screening_criterion_not_found", "未找到硬筛选项", status_code=404)
        row.deleted_at = _now(); row.enabled = False; row.updated_at = _now(); self._audit(actor, "delete", row); self.db.flush()

    def reset(self, actor: User) -> list[dict[str, Any]]:
        self.ensure_defaults()
        for row in self.db.scalars(select(HardScreeningCriterion).where(HardScreeningCriterion.is_builtin.is_(True))):
            default = next(item for item in DEFAULTS if item["code"] == row.code)
            row.name = default["name"]; row.value_mode = default["value_mode"]; row.evaluation_binding = default["evaluation_binding"]
            row.allowed_values_json = {"items": default["allowed_values"]}; row.enabled = True; row.deleted_at = None; row.updated_at = _now()
        self._audit(actor, "reset", None); self.db.flush()
        return self.list()


    @staticmethod
    def _values(values: Any) -> list[str]:
        if not isinstance(values, list):
            raise BusinessError("hard_screening_allowed_values_invalid", "下拉选项必须是数组")
        normalized = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
        if len(normalized) > 100:
            raise BusinessError("hard_screening_allowed_values_too_many", "下拉选项不能超过100项")
        return normalized

    def _validate(self, mode: str, binding: str, values: Any) -> None:
        if mode not in VALUE_MODES:
            raise BusinessError("hard_screening_value_mode_invalid", "取值方式仅支持下拉选择、数值阈值或自由文本")
        if binding not in BINDINGS:
            raise BusinessError("hard_screening_binding_invalid", "不支持的硬筛核验来源")
        normalized = self._values(values)
        if mode == "select" and not normalized:
            raise BusinessError("hard_screening_select_values_missing", "下拉选择至少需要一个可选取值")
        if mode != "select" and normalized:
            raise BusinessError("hard_screening_non_select_values_forbidden", "仅下拉选择可以配置可选取值")
        if mode != BINDING_SPECS[binding][0]:
            raise BusinessError(
                "hard_screening_binding_mode_mismatch",
                "取值方式与所选核验来源不匹配",
            )

    @staticmethod
    def _view(row: HardScreeningCriterion) -> dict[str, Any]:
        return {"criterionId": row.criterion_id, "code": row.code, "name": row.name, "valueMode": row.value_mode, "allowedValues": list((row.allowed_values_json or {}).get("items") or []), "evaluationBinding": row.evaluation_binding, "enabled": row.enabled, "sortOrder": row.sort_order, "isBuiltin": row.is_builtin, "deletedAt": row.deleted_at.isoformat() if row.deleted_at else None}

    def _audit(self, actor: User, action: str, row: HardScreeningCriterion | None) -> None:
        record_audit_event(self.db, actor=actor, action=f"hard_screening.catalog.{action}", target_type="hard_screening_criterion", target_id=row.criterion_id if row else "default", summary=f"硬筛选项目录{action}", details={"criterionCode": row.code if row else None})
