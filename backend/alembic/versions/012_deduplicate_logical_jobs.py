"""deduplicate logical jobs

Revision ID: 012_deduplicate_logical_jobs
Revises: 011_resume_ingestion
"""

from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa

from backend.app.job_identity import job_identity_key, normalize_job_title


revision = "012_deduplicate_logical_jobs"
down_revision = "011_resume_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    jobs = sa.Table("jobs", metadata, autoload_with=bind)
    versions = sa.Table("job_versions", metadata, autoload_with=bind)

    latest_versions = {
        row.job_id: row.latest_at
        for row in bind.execute(
            sa.select(
                versions.c.job_id,
                sa.func.max(versions.c.created_at).label("latest_at"),
            ).group_by(versions.c.job_id)
        )
    }
    groups: dict[tuple[str, str], list] = {}
    for row in bind.execute(sa.select(jobs)).mappings():
        key = (row["department_id"], normalize_job_title(row["title"]))
        groups.setdefault(key, []).append(row)

    for (department_id, _), rows in groups.items():
        canonical = max(
            rows,
            key=lambda row: (
                latest_versions.get(row["job_id"]) or datetime.min,
                row["job_id"],
            ),
        )
        logical_key = job_identity_key(
            department_id=department_id,
            title=canonical["title"],
        )
        for row in rows:
            payload = dict(row["payload"] or {})
            if row["job_id"] == canonical["job_id"]:
                payload["status"] = "active"
                payload.pop("duplicateOf", None)
                payload.pop("archivedReason", None)
                bind.execute(
                    jobs.update()
                    .where(jobs.c.job_id == row["job_id"])
                    .values(identity_key=logical_key, payload=payload)
                )
            elif len(rows) > 1:
                payload["status"] = "archived"
                payload["duplicateOf"] = canonical["job_id"]
                payload["archivedReason"] = "duplicate_logical_job"
                bind.execute(
                    jobs.update()
                    .where(jobs.c.job_id == row["job_id"])
                    .values(payload=payload)
                )


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    jobs = sa.Table("jobs", metadata, autoload_with=bind)
    for row in bind.execute(sa.select(jobs)).mappings():
        payload = dict(row["payload"] or {})
        if payload.get("archivedReason") != "duplicate_logical_job":
            continue
        payload["status"] = "active"
        payload.pop("duplicateOf", None)
        payload.pop("archivedReason", None)
        bind.execute(
            jobs.update()
            .where(jobs.c.job_id == row["job_id"])
            .values(payload=payload)
        )
