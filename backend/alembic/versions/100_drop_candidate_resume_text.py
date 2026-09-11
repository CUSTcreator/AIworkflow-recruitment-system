"""Move legacy candidate resume text to submissions and remove the duplicate column.

Revision ID: 100_drop_candidate_resume_text
Revises: 099_split_organization_only_responsibilities
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "100_drop_candidate_resume_text"
down_revision = "099_split_organization_only_responsibilities"
branch_labels = None
depends_on = None


def _submission_for_candidate(bind, candidate: dict[str, object]) -> str | None:
    current_id = candidate.get("current_resume_submission_id")
    if current_id:
        existing = bind.execute(
            sa.text(
                "SELECT resume_submission_id FROM resume_submissions "
                "WHERE resume_submission_id = :submission_id"
            ),
            {"submission_id": current_id},
        ).scalar_one_or_none()
        if existing:
            return str(existing)

    latest = bind.execute(
        sa.text(
            "SELECT resume_submission_id FROM resume_submissions "
            "WHERE candidate_id = :candidate_id "
            "ORDER BY updated_at DESC, resume_submission_id DESC LIMIT 1"
        ),
        {"candidate_id": candidate["candidate_id"]},
    ).scalar_one_or_none()
    return str(latest) if latest else None


def _preserve_legacy_text(bind) -> None:
    candidates = bind.execute(
        sa.text(
            "SELECT candidate_id, current_resume_submission_id, resume_text "
            "FROM candidates WHERE resume_text IS NOT NULL"
        )
    ).mappings()
    for candidate in candidates:
        legacy_text = str(candidate["resume_text"] or "")
        if not legacy_text.strip():
            continue
        submission_id = _submission_for_candidate(bind, dict(candidate))
        if submission_id is None:
            raise RuntimeError(
                "无法删除 candidates.resume_text：候选人 "
                f"{candidate['candidate_id']} 存在旧简历文本，但没有对应 ResumeSubmission。"
            )
        parsed_text = bind.execute(
            sa.text(
                "SELECT parsed_text FROM resume_submissions "
                "WHERE resume_submission_id = :submission_id"
            ),
            {"submission_id": submission_id},
        ).scalar_one_or_none()
        if not str(parsed_text or "").strip():
            bind.execute(
                sa.text(
                    "UPDATE resume_submissions SET parsed_text = :parsed_text "
                    "WHERE resume_submission_id = :submission_id"
                ),
                {"parsed_text": legacy_text, "submission_id": submission_id},
            )


def upgrade() -> None:
    _preserve_legacy_text(op.get_bind())
    with op.batch_alter_table("candidates") as batch_op:
        batch_op.drop_column("resume_text")


def downgrade() -> None:
    with op.batch_alter_table("candidates") as batch_op:
        batch_op.add_column(
            sa.Column("resume_text", sa.Text(), nullable=False, server_default="")
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE candidates SET resume_text = COALESCE(("
            "SELECT parsed_text FROM resume_submissions "
            "WHERE resume_submissions.resume_submission_id = "
            "candidates.current_resume_submission_id"
            "), '')"
        )
    )
    with op.batch_alter_table("candidates") as batch_op:
        batch_op.alter_column("resume_text", server_default=None)
