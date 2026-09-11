"""canonical current job fields

Revision ID: 038_canonical_job_fields
Revises: 037_merge_intake_snapshots
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "038_canonical_job_fields"
down_revision = "037_merge_intake_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("source_document_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("responsibilities", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("qualifications", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("education_requirement", sa.String(length=255), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_source_document", "source_documents", ["source_document_id"], ["source_document_id"]
        )
        batch.create_index("ix_jobs_source_document_id", ["source_document_id"])
    with op.batch_alter_table("job_versions") as batch:
        batch.add_column(sa.Column("snapshot", sa.JSON(), nullable=True))

    connection = op.get_bind()
    jobs = connection.execute(sa.text("SELECT job_id, payload FROM jobs")).mappings().all()
    for job in jobs:
        draft = connection.execute(
            sa.text(
                "SELECT source_document_id, responsibilities, qualifications, education_requirement "
                "FROM job_drafts WHERE confirmed_job_id=:job_id "
                "ORDER BY updated_at DESC LIMIT 1"
            ),
            {"job_id": job["job_id"]},
        ).mappings().first()
        payload = job["payload"] or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        connection.execute(
            sa.text(
                "UPDATE jobs SET source_document_id=:source_document_id, "
                "responsibilities=:responsibilities, qualifications=:qualifications, "
                "education_requirement=:education_requirement WHERE job_id=:job_id"
            ),
            {
                "job_id": job["job_id"],
                "source_document_id": (draft or {}).get("source_document_id") or payload.get("sourceDocumentId"),
                "responsibilities": json.dumps((draft or {}).get("responsibilities") or payload.get("responsibilities") or []),
                "qualifications": json.dumps((draft or {}).get("qualifications") or payload.get("qualifications") or []),
                "education_requirement": (draft or {}).get("education_requirement") or payload.get("educationRequirement"),
            },
        )

    versions = connection.execute(
        sa.text("SELECT jd_version_id, job_id, source_text FROM job_versions")
    ).mappings().all()
    for version in versions:
        job = connection.execute(
            sa.text(
                "SELECT title, headcount, source_document_id, responsibilities, qualifications, "
                "education_requirement, major_requirement FROM jobs WHERE job_id=:job_id"
            ),
            {"job_id": version["job_id"]},
        ).mappings().first()
        if job is None:
            continue
        snapshot = dict(job)
        snapshot["jd_text"] = version["source_text"]
        connection.execute(
            sa.text("UPDATE job_versions SET snapshot=:snapshot WHERE jd_version_id=:version_id"),
            {"version_id": version["jd_version_id"], "snapshot": json.dumps(snapshot, ensure_ascii=False)},
        )


def downgrade() -> None:
    with op.batch_alter_table("job_versions") as batch:
        batch.drop_column("snapshot")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_source_document_id")
        batch.drop_constraint("fk_jobs_source_document", type_="foreignkey")
        batch.drop_column("education_requirement")
        batch.drop_column("qualifications")
        batch.drop_column("responsibilities")
        batch.drop_column("source_document_id")
