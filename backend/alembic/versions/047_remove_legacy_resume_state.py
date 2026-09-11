"""移除已停用的 Candidate 与 Application 简历重建状态列。

Revision ID: 047_remove_legacy_resume_state
Revises: 046_resume_submission_states
"""

from alembic import op
import sqlalchemy as sa


revision = "047_remove_legacy_resume_state"
down_revision = "046_resume_submission_states"
branch_labels = None
depends_on = None


_CANDIDATE_COLUMNS = (
    "resume_intake_status",
    "resume_rebuild_status",
    "resume_rebuild_submission_id",
    "resume_rebuild_mode",
    "resume_rebuild_message",
)
_APPLICATION_COLUMNS = (
    "resume_rebuild_status",
    "resume_rebuild_submission_id",
    "resume_rebuild_message",
)


def upgrade() -> None:
    # 状态事实已完全迁移到 ResumeSubmission，Application 读模型按 Candidate 当前
    # Submission 与 adopted_resume_submission_id 推导重建提示，因此这些列不再保存事实。
    for column in _CANDIDATE_COLUMNS:
        op.drop_column("candidates", column)
    for column in _APPLICATION_COLUMNS:
        op.drop_column("applications", column)


def downgrade() -> None:
    op.add_column("candidates", sa.Column("resume_intake_status", sa.String(32), nullable=False, server_default="processing"))
    op.add_column("candidates", sa.Column("resume_rebuild_status", sa.String(32), nullable=False, server_default="idle"))
    op.add_column("candidates", sa.Column("resume_rebuild_submission_id", sa.String(64), nullable=True))
    op.add_column("candidates", sa.Column("resume_rebuild_mode", sa.String(32), nullable=True))
    op.add_column("candidates", sa.Column("resume_rebuild_message", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("resume_rebuild_status", sa.String(32), nullable=False, server_default="idle"))
    op.add_column("applications", sa.Column("resume_rebuild_submission_id", sa.String(64), nullable=True))
    op.add_column("applications", sa.Column("resume_rebuild_message", sa.Text(), nullable=True))