"""persist job identity, JD versions, and reusable job profiles

Revision ID: 007_job_identity_and_versions
Revises: 006_interview_records
"""

from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa

from backend.app.job_identity import jd_content_sha256, normalize_jd_text


revision = "007_job_identity_and_versions"
down_revision = "006_interview_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    job_columns = {item["name"] for item in inspector.get_columns("jobs")}
    with op.batch_alter_table("jobs") as batch_op:
        if "external_job_id" not in job_columns:
            batch_op.add_column(sa.Column("external_job_id", sa.String(128), nullable=True))
        if "identity_key" not in job_columns:
            batch_op.add_column(sa.Column("identity_key", sa.String(64), nullable=True))
        if "jd_content_sha256" not in job_columns:
            batch_op.add_column(sa.Column("jd_content_sha256", sa.String(64), nullable=True))

    inspector = sa.inspect(bind)
    if "job_versions" not in inspector.get_table_names():
        op.create_table(
            "job_versions",
            sa.Column("jd_version_id", sa.String(96), primary_key=True),
            sa.Column(
                "job_id", sa.String(64), sa.ForeignKey("jobs.job_id"), nullable=False
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("source_sha256", sa.String(64), nullable=False),
            sa.Column("source_text", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "job_id", "source_sha256", name="uq_job_version_job_source"
            ),
            sa.UniqueConstraint(
                "job_id", "version", name="uq_job_version_job_number"
            ),
        )
        op.create_index("ix_job_versions_job_id", "job_versions", ["job_id"])
        op.create_index(
            "ix_job_versions_source_sha256", "job_versions", ["source_sha256"]
        )

    application_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("applications")
    }
    if "jd_version_id" not in application_columns:
        with op.batch_alter_table("applications") as batch_op:
            batch_op.add_column(sa.Column("jd_version_id", sa.String(96), nullable=True))

    profile_columns = {
        item["name"]
        for item in sa.inspect(bind).get_columns("job_requirement_profiles")
    }
    if "jd_version_id" not in profile_columns:
        with op.batch_alter_table("job_requirement_profiles") as batch_op:
            batch_op.add_column(sa.Column("jd_version_id", sa.String(96), nullable=True))

    jobs = sa.table(
        "jobs",
        sa.column("job_id", sa.String),
        sa.column("jd_text", sa.Text),
        sa.column("identity_key", sa.String),
        sa.column("jd_content_sha256", sa.String),
    )
    versions = sa.table(
        "job_versions",
        sa.column("jd_version_id", sa.String),
        sa.column("job_id", sa.String),
        sa.column("version", sa.Integer),
        sa.column("source_sha256", sa.String),
        sa.column("source_text", sa.Text),
        sa.column("created_at", sa.DateTime),
    )
    applications = sa.table(
        "applications",
        sa.column("job_id", sa.String),
        sa.column("jd_version_id", sa.String),
    )
    profiles = sa.table(
        "job_requirement_profiles",
        sa.column("job_id", sa.String),
        sa.column("source_sha256", sa.String),
        sa.column("jd_version_id", sa.String),
    )
    now = sa.func.now()
    for row in bind.execute(sa.select(jobs.c.job_id, jobs.c.jd_text)).mappings():
        source_text = normalize_jd_text(row["jd_text"] or "")
        source_hash = jd_content_sha256(source_text)
        version_id = f"JDV_{row['job_id']}_{source_hash[:12]}"
        legacy_identity = hashlib.sha256(
            f"legacy:{row['job_id']}".encode("utf-8")
        ).hexdigest()
        bind.execute(
            jobs.update()
            .where(jobs.c.job_id == row["job_id"])
            .values(
                jd_text=source_text,
                identity_key=legacy_identity,
                jd_content_sha256=source_hash,
            )
        )
        bind.execute(
            versions.insert().values(
                jd_version_id=version_id,
                job_id=row["job_id"],
                version=1,
                source_sha256=source_hash,
                source_text=source_text,
                created_at=now,
            )
        )
        bind.execute(
            applications.update()
            .where(applications.c.job_id == row["job_id"])
            .values(jd_version_id=version_id)
        )
        bind.execute(
            profiles.update()
            .where(
                profiles.c.job_id == row["job_id"],
                profiles.c.source_sha256 == source_hash,
            )
            .values(jd_version_id=version_id)
        )

    inspector = sa.inspect(bind)
    job_indexes = {item["name"] for item in inspector.get_indexes("jobs")}
    job_uniques = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("jobs")
    }
    if (
        "ix_jobs_external_job_id" not in job_indexes
        or "ix_jobs_jd_content_sha256" not in job_indexes
        or ("identity_key",) not in job_uniques
    ):
        with op.batch_alter_table("jobs") as batch_op:
            if "ix_jobs_external_job_id" not in job_indexes:
                batch_op.create_index("ix_jobs_external_job_id", ["external_job_id"])
            if "ix_jobs_jd_content_sha256" not in job_indexes:
                batch_op.create_index("ix_jobs_jd_content_sha256", ["jd_content_sha256"])
            if ("identity_key",) not in job_uniques:
                batch_op.create_unique_constraint("uq_jobs_identity_key", ["identity_key"])

    inspector = sa.inspect(bind)
    application_indexes = {
        item["name"] for item in inspector.get_indexes("applications")
    }
    application_foreign_keys = {
        tuple(item.get("constrained_columns") or ())
        for item in inspector.get_foreign_keys("applications")
    }
    if (
        "ix_applications_jd_version_id" not in application_indexes
        or ("jd_version_id",) not in application_foreign_keys
    ):
        with op.batch_alter_table("applications") as batch_op:
            if "ix_applications_jd_version_id" not in application_indexes:
                batch_op.create_index("ix_applications_jd_version_id", ["jd_version_id"])
            if ("jd_version_id",) not in application_foreign_keys:
                batch_op.create_foreign_key(
                    "fk_applications_jd_version_id",
                    "job_versions",
                    ["jd_version_id"],
                    ["jd_version_id"],
                )

    inspector = sa.inspect(bind)
    profile_indexes = {
        item["name"] for item in inspector.get_indexes("job_requirement_profiles")
    }
    profile_foreign_keys = {
        tuple(item.get("constrained_columns") or ())
        for item in inspector.get_foreign_keys("job_requirement_profiles")
    }
    profile_uniques = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("job_requirement_profiles")
    }
    profile_unique_columns = ("job_id", "source_sha256", "version")
    if (
        "ix_job_requirement_profiles_jd_version_id" not in profile_indexes
        or ("jd_version_id",) not in profile_foreign_keys
        or profile_unique_columns not in profile_uniques
    ):
        with op.batch_alter_table("job_requirement_profiles") as batch_op:
            if "ix_job_requirement_profiles_jd_version_id" not in profile_indexes:
                batch_op.create_index(
                    "ix_job_requirement_profiles_jd_version_id", ["jd_version_id"]
                )
            if ("jd_version_id",) not in profile_foreign_keys:
                batch_op.create_foreign_key(
                    "fk_job_requirement_profiles_jd_version_id",
                    "job_versions",
                    ["jd_version_id"],
                    ["jd_version_id"],
                )
            if profile_unique_columns not in profile_uniques:
                batch_op.create_unique_constraint(
                    "uq_job_requirement_profile_source_version",
                    list(profile_unique_columns),
                )


def downgrade() -> None:
    with op.batch_alter_table("job_requirement_profiles") as batch_op:
        batch_op.drop_constraint(
            "uq_job_requirement_profile_source_version", type_="unique"
        )
        batch_op.drop_constraint(
            "fk_job_requirement_profiles_jd_version_id", type_="foreignkey"
        )
        batch_op.drop_index("ix_job_requirement_profiles_jd_version_id")
        batch_op.drop_column("jd_version_id")
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_constraint("fk_applications_jd_version_id", type_="foreignkey")
        batch_op.drop_index("ix_applications_jd_version_id")
        batch_op.drop_column("jd_version_id")
    op.drop_index("ix_job_versions_source_sha256", table_name="job_versions")
    op.drop_index("ix_job_versions_job_id", table_name="job_versions")
    op.drop_table("job_versions")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_constraint("uq_jobs_identity_key", type_="unique")
        batch_op.drop_index("ix_jobs_jd_content_sha256")
        batch_op.drop_index("ix_jobs_external_job_id")
        batch_op.drop_column("jd_content_sha256")
        batch_op.drop_column("identity_key")
        batch_op.drop_column("external_job_id")
