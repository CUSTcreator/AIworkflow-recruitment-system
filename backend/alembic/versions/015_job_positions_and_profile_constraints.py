"""separate job positions from hiring requisitions

Revision ID: 015_job_positions
Revises: 014_system_admin
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from alembic import op
import sqlalchemy as sa

from backend.app.job_identity import normalize_job_title


revision = "015_job_positions"
down_revision = "014_system_admin"
branch_labels = None
depends_on = None


def _position_id(department_id: str, normalized_title: str) -> str:
    source = f"{department_id}:{normalized_title}"
    return f"POS_{hashlib.sha256(source.encode('utf-8')).hexdigest()[:12].upper()}"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    job_columns = {item["name"] for item in inspector.get_columns("jobs")}
    profile_constraints = {
        item["name"]
        for item in inspector.get_unique_constraints(
            "job_requirement_profiles"
        )
    }
    # The legacy 001 migration builds current ORM metadata on a brand-new
    # database. In that case the target schema already exists and this
    # migration only needs to be stamped.
    if (
        inspector.has_table("job_positions")
        and {"position_id", "opened_at", "closed_at"} <= job_columns
        and "uq_job_requirement_profile_job_version" in profile_constraints
        and "uq_job_requirement_profile_job_source" in profile_constraints
    ):
        return

    op.create_table(
        "job_positions",
        sa.Column("position_id", sa.String(64), primary_key=True),
        sa.Column(
            "department_id",
            sa.String(64),
            sa.ForeignKey("departments.department_id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("normalized_title", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "department_id",
            "normalized_title",
            name="uq_job_position_department_title",
        ),
    )
    op.create_index(
        "ix_job_positions_department_id",
        "job_positions",
        ["department_id"],
    )

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("position_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("opened_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))

    metadata = sa.MetaData()
    positions = sa.Table("job_positions", metadata, autoload_with=bind)
    jobs = sa.Table("jobs", metadata, autoload_with=bind)
    versions = sa.Table("job_versions", metadata, autoload_with=bind)
    now = datetime.utcnow()

    rows = list(bind.execute(sa.select(jobs)).mappings())
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        normalized = normalize_job_title(row["title"])
        groups.setdefault((row["department_id"], normalized), []).append(row)

    for (department_id, normalized), group in groups.items():
        canonical = next(
            (
                row
                for row in group
                if row["status"] == "open"
                and not dict(row["payload"] or {}).get("duplicateOf")
            ),
            next(
                (
                    row
                    for row in group
                    if not dict(row["payload"] or {}).get("duplicateOf")
                ),
                group[-1],
            ),
        )
        position_id = _position_id(department_id, normalized)
        bind.execute(
            positions.insert().values(
                position_id=position_id,
                department_id=department_id,
                title=canonical["title"],
                normalized_title=normalized,
                created_at=now,
                updated_at=now,
            )
        )
        open_rows = [row for row in group if row["status"] == "open"]
        keep_open_id = canonical["job_id"] if open_rows else None
        if keep_open_id not in {row["job_id"] for row in open_rows} and open_rows:
            keep_open_id = open_rows[-1]["job_id"]
        for row in group:
            earliest_version = bind.scalar(
                sa.select(sa.func.min(versions.c.created_at)).where(
                    versions.c.job_id == row["job_id"]
                )
            )
            values = {
                "position_id": position_id,
                "opened_at": earliest_version or now,
                "closed_at": now if row["status"] == "closed" else None,
            }
            if (
                row["status"] == "open"
                and keep_open_id
                and row["job_id"] != keep_open_id
            ):
                values["status"] = "closed"
                values["closed_at"] = now
                payload = dict(row["payload"] or {})
                payload["status"] = "archived"
                payload["archivedReason"] = "duplicate_open_requisition"
                payload["duplicateOf"] = keep_open_id
                values["payload"] = payload
            bind.execute(
                jobs.update()
                .where(jobs.c.job_id == row["job_id"])
                .values(**values)
            )

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("position_id", existing_type=sa.String(64), nullable=False)
        batch_op.alter_column("opened_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.create_foreign_key(
            "fk_jobs_position_id_job_positions",
            "job_positions",
            ["position_id"],
            ["position_id"],
        )
        batch_op.create_index("ix_jobs_position_id", ["position_id"])

    op.create_index(
        "uq_jobs_one_open_per_position",
        "jobs",
        ["position_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )

    _repair_job_profile_versions(bind)
    with op.batch_alter_table("job_requirement_profiles") as batch_op:
        batch_op.drop_constraint(
            "uq_job_requirement_profile_source_version",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_job_requirement_profile_job_version",
            ["job_id", "version"],
        )
        batch_op.create_unique_constraint(
            "uq_job_requirement_profile_job_source",
            ["job_id", "source_sha256"],
        )

    with op.batch_alter_table("resume_profiles") as batch_op:
        batch_op.create_unique_constraint(
            "uq_resume_profile_candidate_version",
            ["candidate_id", "version"],
        )
        batch_op.create_unique_constraint(
            "uq_resume_profile_candidate_source",
            ["candidate_id", "source_sha256"],
        )

    with op.batch_alter_table("resume_submissions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_resume_submission_document_job",
            ["source_document_id", "job_id"],
        )
    op.create_index(
        "uq_resume_submission_external_application",
        "resume_submissions",
        ["external_application_id"],
        unique=True,
        postgresql_where=sa.text("external_application_id IS NOT NULL"),
        sqlite_where=sa.text("external_application_id IS NOT NULL"),
    )
    with op.batch_alter_table("applications") as batch_op:
        batch_op.create_unique_constraint(
            "uq_application_candidate_requisition",
            ["candidate_id", "job_id"],
        )
    with op.batch_alter_table("application_assignments") as batch_op:
        batch_op.create_unique_constraint(
            "uq_application_assignment",
            ["application_id", "user_id", "assignment_type"],
        )
    with op.batch_alter_table("interviews") as batch_op:
        batch_op.create_unique_constraint(
            "uq_interview_application_type",
            ["application_id", "interview_type"],
        )
    op.create_index(
        "uq_tasks_active_application_type",
        "tasks",
        ["application_id", "task_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'in_progress')"),
        sqlite_where=sa.text("status IN ('pending', 'in_progress')"),
    )
    op.create_index(
        "uq_workflow_active_subject_type",
        "workflow_runs",
        ["workflow_type", "subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'running') AND subject_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "status IN ('pending', 'running') AND subject_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_workflow_active_application_type",
        "workflow_runs",
        ["workflow_type", "application_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'running') "
            "AND subject_id IS NULL AND application_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "status IN ('pending', 'running') "
            "AND subject_id IS NULL AND application_id IS NOT NULL"
        ),
    )
    with op.batch_alter_table("workflow_artifacts") as batch_op:
        batch_op.create_unique_constraint(
            "uq_workflow_artifact_run_type_version",
            ["workflow_run_id", "artifact_type", "version"],
        )


def _repair_job_profile_versions(bind) -> None:
    metadata = sa.MetaData()
    profiles = sa.Table("job_requirement_profiles", metadata, autoload_with=bind)
    capability_profiles = sa.Table(
        "candidate_capability_profiles", metadata, autoload_with=bind
    )
    rows = list(
        bind.execute(
            sa.select(profiles).order_by(
                profiles.c.job_id,
                profiles.c.created_at,
                profiles.c.job_profile_id,
            )
        ).mappings()
    )
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["job_id"], []).append(row)
    for job_rows in grouped.values():
        by_source: dict[str, dict] = {}
        retained: list[dict] = []
        for row in job_rows:
            canonical = by_source.get(row["source_sha256"])
            if canonical is None:
                by_source[row["source_sha256"]] = row
                retained.append(row)
                continue
            bind.execute(
                capability_profiles.update()
                .where(
                    capability_profiles.c.job_profile_id == row["job_profile_id"]
                )
                .values(job_profile_id=canonical["job_profile_id"])
            )
            bind.execute(
                profiles.delete().where(
                    profiles.c.job_profile_id == row["job_profile_id"]
                )
            )
        for version, row in enumerate(retained, start=1):
            bind.execute(
                profiles.update()
                .where(profiles.c.job_profile_id == row["job_profile_id"])
                .values(version=version)
            )


def downgrade() -> None:
    with op.batch_alter_table("workflow_artifacts") as batch_op:
        batch_op.drop_constraint(
            "uq_workflow_artifact_run_type_version", type_="unique"
        )
    op.drop_index(
        "uq_workflow_active_application_type", table_name="workflow_runs"
    )
    op.drop_index("uq_workflow_active_subject_type", table_name="workflow_runs")
    op.drop_index("uq_tasks_active_application_type", table_name="tasks")
    with op.batch_alter_table("interviews") as batch_op:
        batch_op.drop_constraint(
            "uq_interview_application_type", type_="unique"
        )
    with op.batch_alter_table("application_assignments") as batch_op:
        batch_op.drop_constraint("uq_application_assignment", type_="unique")
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_constraint(
            "uq_application_candidate_requisition", type_="unique"
        )
    op.drop_index(
        "uq_resume_submission_external_application",
        table_name="resume_submissions",
    )
    with op.batch_alter_table("resume_submissions") as batch_op:
        batch_op.drop_constraint(
            "uq_resume_submission_document_job", type_="unique"
        )
    with op.batch_alter_table("resume_profiles") as batch_op:
        batch_op.drop_constraint(
            "uq_resume_profile_candidate_source", type_="unique"
        )
        batch_op.drop_constraint(
            "uq_resume_profile_candidate_version", type_="unique"
        )
    with op.batch_alter_table("job_requirement_profiles") as batch_op:
        batch_op.drop_constraint(
            "uq_job_requirement_profile_job_source", type_="unique"
        )
        batch_op.drop_constraint(
            "uq_job_requirement_profile_job_version", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_job_requirement_profile_source_version",
            ["job_id", "source_sha256", "version"],
        )
    op.drop_index("uq_jobs_one_open_per_position", table_name="jobs")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_position_id")
        batch_op.drop_constraint(
            "fk_jobs_position_id_job_positions", type_="foreignkey"
        )
        batch_op.drop_column("closed_at")
        batch_op.drop_column("opened_at")
        batch_op.drop_column("position_id")
    op.drop_index("ix_job_positions_department_id", table_name="job_positions")
    op.drop_table("job_positions")
