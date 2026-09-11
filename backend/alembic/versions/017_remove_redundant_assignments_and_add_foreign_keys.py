"""remove redundant assignments and enforce result ownership

Revision ID: 017_result_ownership
Revises: 016_remove_role_tables
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "017_result_ownership"
down_revision = "016_remove_role_tables"
branch_labels = None
depends_on = None


PAYLOAD_TABLES = (
    "screening_assessments",
    "requirement_assessments",
    "risks",
    "verification_targets",
    "interview_guides",
    "interview_questions",
    "first_interview_progress_drafts",
    "second_interview_progress_drafts",
    "interviewer_raw_notes",
    "question_responses",
    "interview_assessments",
    "interview_evidence",
    "risk_updates",
    "hr_second_review_packages",
    "hr_interview_assessments",
    "final_candidate_review_packages",
)


def _has_foreign_key(
    inspector: sa.Inspector,
    table_name: str,
    column_name: str,
    referred_table: str,
) -> bool:
    return any(
        item.get("constrained_columns") == [column_name]
        and item.get("referred_table") == referred_table
        for item in inspector.get_foreign_keys(table_name)
    )


def _add_foreign_key(
    table_name: str,
    column_name: str,
    referred_table: str,
    referred_column: str,
    constraint_name: str,
) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name) or _has_foreign_key(
        inspector, table_name, column_name, referred_table
    ):
        return
    orphan_count = bind.scalar(
        sa.text(
            f'SELECT count(*) FROM "{table_name}" AS source '
            f'LEFT JOIN "{referred_table}" AS target '
            f'ON target."{referred_column}" = source."{column_name}" '
            f'WHERE source."{column_name}" IS NOT NULL '
            f'AND target."{referred_column}" IS NULL'
        )
    )
    if orphan_count:
        raise RuntimeError(
            f"cannot add {constraint_name}: {orphan_count} orphan rows"
        )
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.create_foreign_key(
            constraint_name,
            referred_table,
            [column_name],
            [referred_column],
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in ("application_assignments", "interview_assignments"):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
            inspector = sa.inspect(op.get_bind())

    if inspector.has_table("candidate_second_round_analyses"):
        row_count = op.get_bind().scalar(
            sa.text("SELECT count(*) FROM candidate_second_round_analyses")
        )
        if not row_count:
            op.drop_table("candidate_second_round_analyses")

    _add_foreign_key(
        "users",
        "department_id",
        "departments",
        "department_id",
        "fk_users_department",
    )
    _add_foreign_key(
        "workflow_runs",
        "triggered_by",
        "users",
        "user_id",
        "fk_workflow_runs_triggered_by",
    )
    _add_foreign_key(
        "hard_screening_policies",
        "created_by",
        "users",
        "user_id",
        "fk_hard_screening_policies_created_by",
    )
    _add_foreign_key(
        "idempotency_keys",
        "user_id",
        "users",
        "user_id",
        "fk_idempotency_keys_user",
    )
    for table_name in PAYLOAD_TABLES:
        _add_foreign_key(
            table_name,
            "application_id",
            "applications",
            "application_id",
            f"fk_{table_name}_application",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    foreign_keys = [
        (
            "users",
            "fk_users_department",
        ),
        (
            "workflow_runs",
            "fk_workflow_runs_triggered_by",
        ),
        (
            "hard_screening_policies",
            "fk_hard_screening_policies_created_by",
        ),
        (
            "idempotency_keys",
            "fk_idempotency_keys_user",
        ),
        *[
            (table_name, f"fk_{table_name}_application")
            for table_name in PAYLOAD_TABLES
        ],
    ]
    for table_name, constraint_name in reversed(foreign_keys):
        if not inspector.has_table(table_name):
            continue
        names = {
            item.get("name")
            for item in inspector.get_foreign_keys(table_name)
        }
        if constraint_name in names:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.drop_constraint(constraint_name, type_="foreignkey")
            inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("application_assignments"):
        op.create_table(
            "application_assignments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "application_id",
                sa.String(64),
                sa.ForeignKey("applications.application_id"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(64),
                sa.ForeignKey("users.user_id"),
                nullable=False,
            ),
            sa.Column("assignment_type", sa.String(64), nullable=False),
            sa.UniqueConstraint(
                "application_id",
                "user_id",
                "assignment_type",
                name="uq_application_assignment",
            ),
        )
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("interview_assignments"):
        op.create_table(
            "interview_assignments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "interview_id",
                sa.String(64),
                sa.ForeignKey("interviews.interview_id"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(64),
                sa.ForeignKey("users.user_id"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "interview_id",
                "user_id",
                name="uq_interview_user",
            ),
        )
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("candidate_second_round_analyses"):
        op.create_table(
            "candidate_second_round_analyses",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("application_id", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
