"""separate failed automatic screening from submitted applications

Revision ID: 018_screening_failure
Revises: 017_result_ownership
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "018_screening_failure"
down_revision = "017_result_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    assessments = sa.table(
        "screening_assessments",
        sa.column("application_id", sa.String),
        sa.column("payload", sa.JSON),
        sa.column("created_at", sa.DateTime),
    )
    latest: dict[str, dict] = {}
    rows = bind.execute(
        sa.select(
            assessments.c.application_id,
            assessments.c.payload,
        ).order_by(assessments.c.created_at)
    ).mappings()
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        latest[str(row["application_id"])] = (
            payload if isinstance(payload, dict) else {}
        )

    applications = sa.table(
        "applications",
        sa.column("application_id", sa.String),
        sa.column("status", sa.String),
    )
    failed_ids = [
        application_id
        for application_id, payload in latest.items()
        if payload.get("scoreStatus") == "failed"
    ]
    if failed_ids:
        bind.execute(
            applications.update()
            .where(
                applications.c.application_id.in_(failed_ids),
                applications.c.status == "submitted",
            )
            .values(status="screening_failed")
        )

    tasks = sa.table(
        "tasks",
        sa.column("task_type", sa.String),
        sa.column("status", sa.String),
    )
    bind.execute(
        tasks.update()
        .where(
            tasks.c.task_type == "run_scoring",
            tasks.c.status != "done",
        )
        .values(status="done")
    )


def downgrade() -> None:
    applications = sa.table(
        "applications",
        sa.column("status", sa.String),
    )
    op.get_bind().execute(
        applications.update()
        .where(applications.c.status == "screening_failed")
        .values(status="submitted")
    )
