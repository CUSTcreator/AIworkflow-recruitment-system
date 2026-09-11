"""merge wrapped job draft list lines

Revision ID: 063_merge_job_draft_lines
Revises: 062_normalize_job_draft_lists
"""
from __future__ import annotations

import re
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "063_merge_job_draft_lines"
down_revision = "062_normalize_job_draft_lists"
branch_labels = None
depends_on = None


def _merge_wrapped_lines(value: Any) -> list[str] | None:
    """把旧规则因 Excel 单元格折行拆碎的同一编号条目重新合并。"""
    if not isinstance(value, list):
        return None
    result: list[str] = []
    current = ""
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        current += text
        if re.search(r"[；。！？!?]$", text):
            result.append(current)
            current = ""
    if current:
        result.append(current)
    return result


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT job_draft_id, responsibilities, qualifications FROM job_drafts")
    ).mappings()
    statement = sa.text(
        "UPDATE job_drafts SET responsibilities = :responsibilities, qualifications = :qualifications "
        "WHERE job_draft_id = :job_draft_id"
    ).bindparams(
        sa.bindparam("responsibilities", type_=sa.JSON()),
        sa.bindparam("qualifications", type_=sa.JSON()),
    )
    for row in rows:
        responsibilities = _merge_wrapped_lines(row["responsibilities"])
        qualifications = _merge_wrapped_lines(row["qualifications"])
        if responsibilities == row["responsibilities"] and qualifications == row["qualifications"]:
            continue
        bind.execute(statement, {
            "job_draft_id": row["job_draft_id"],
            "responsibilities": responsibilities if responsibilities is not None else row["responsibilities"],
            "qualifications": qualifications if qualifications is not None else row["qualifications"],
        })


def downgrade() -> None:
    # 数据纠正迁移不反向制造折行碎片。
    pass