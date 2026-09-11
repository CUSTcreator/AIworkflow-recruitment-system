"""normalize historical job draft text lists

Revision ID: 062_normalize_job_draft_lists
Revises: 061_audit_event_correlation
"""
from __future__ import annotations

import ast
import json
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "062_normalize_job_draft_lists"
down_revision = "061_audit_event_correlation"
branch_labels = None
depends_on = None


def _as_text_list(value: Any) -> list[str] | None:
    """仅处理旧实现误存的 Python/JSON 列表字符串，不猜测其他格式。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return None
    text = value.strip()
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, (list, tuple)):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return None


def _major_from_qualifications(items: list[str]) -> str | None:
    """与岗位导入规则保持一致：只接受明确列举专业的条目。"""
    import re

    for item in items:
        text = item.strip()
        is_named_major_list = bool(re.search(r"(?:及其|等|相关)专业(?:[；。]|$)", text))
        is_explicit_field = bool(re.match(r"^(?:所学|相关)?专业\s*[：:]", text))
        is_generic_knowledge = bool(re.search(r"(?:掌握|熟悉|了解|具备|基础知识|专业知识|学习成绩)", text))
        if (is_named_major_list or is_explicit_field) and not is_generic_knowledge:
            return text[:500]
    return None


def upgrade() -> None:
    """把历史草稿的职责/资格从 JSON 字符串恢复为真正的 JSON 数组。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT job_draft_id, responsibilities, qualifications, major_requirement, payload FROM job_drafts")
    ).mappings()
    statement = sa.text(
        "UPDATE job_drafts "
        "SET responsibilities = :responsibilities, qualifications = :qualifications, "
        "major_requirement = :major_requirement "
        "WHERE job_draft_id = :job_draft_id"
    ).bindparams(
        sa.bindparam("responsibilities", type_=sa.JSON()),
        sa.bindparam("qualifications", type_=sa.JSON()),
    )
    for row in rows:
        responsibilities = _as_text_list(row["responsibilities"])
        qualifications = _as_text_list(row["qualifications"])
        normalized_qualifications = qualifications if qualifications is not None else row["qualifications"]
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        major_requirement = row["major_requirement"]
        if payload.get("major_source") == "qualification_heuristic" and isinstance(normalized_qualifications, list):
            major_requirement = _major_from_qualifications(normalized_qualifications)
        if responsibilities is None and qualifications is None and major_requirement == row["major_requirement"]:
            continue
        bind.execute(statement, {
            "job_draft_id": row["job_draft_id"],
            "responsibilities": responsibilities if responsibilities is not None else row["responsibilities"],
            "qualifications": normalized_qualifications,
            "major_requirement": major_requirement,
        })


def downgrade() -> None:
    # 该迁移是数据纠正；降级不把正确数组重新破坏为字符串。
    pass