"""add job profile workflow state

Revision ID: 054_job_profile_workflow
Revises: 053_workflow_step_checkpoints
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "054_job_profile_workflow"
down_revision = "053_workflow_step_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为每一版 JD 增加岗位画像运行状态，并补齐历史画像的版本外键。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    version_columns = {column["name"] for column in inspector.get_columns("job_versions")}
    if "profile_status" not in version_columns:
        with op.batch_alter_table("job_versions") as batch:
            batch.add_column(
                sa.Column(
                    "profile_status",
                    sa.String(length=32),
                    nullable=False,
                    server_default="queued",
                )
            )
            batch.add_column(sa.Column("profile_error_message", sa.Text(), nullable=True))
            batch.add_column(sa.Column("profile_started_at", sa.DateTime(), nullable=True))
            batch.add_column(sa.Column("profile_completed_at", sa.DateTime(), nullable=True))
            batch.create_index(
                "ix_job_versions_profile_status",
                ["profile_status"],
            )

    # 先按 job_id + source_sha256 回填历史 Profile 的确切 JD 版本，再收紧新数据约束。
    profile_columns = {
        column["name"] for column in inspector.get_columns("job_requirement_profiles")
    }
    if "jd_version_id" in profile_columns:
        op.execute(
            """
            UPDATE job_requirement_profiles
            SET jd_version_id = (
                SELECT job_versions.jd_version_id
                FROM job_versions
                WHERE job_versions.job_id = job_requirement_profiles.job_id
                  AND job_versions.source_sha256 = job_requirement_profiles.source_sha256
                ORDER BY job_versions.version DESC
                LIMIT 1
            )
            WHERE jd_version_id IS NULL
            """
        )
        # 更早的历史画像创建于 JD 文本哈希规范化之前：哈希无法相等时，
        # 同一岗位的同版本号仍是唯一、可追溯的冻结来源。只回填空外键，
        # 不覆盖已经显式绑定的历史记录。
        op.execute(
            """
            UPDATE job_requirement_profiles
            SET jd_version_id = (
                SELECT job_versions.jd_version_id
                FROM job_versions
                WHERE job_versions.job_id = job_requirement_profiles.job_id
                  AND job_versions.version = job_requirement_profiles.version
                LIMIT 1
            )
            WHERE jd_version_id IS NULL
            """
        )
        unresolved = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM job_requirement_profiles "
                "WHERE jd_version_id IS NULL"
            )
        ).scalar_one()
        if unresolved:
            raise RuntimeError(
                "job_requirement_profile_version_backfill_incomplete:"
                f"{unresolved}"
            )
        nullable = next(
            column["nullable"]
            for column in inspector.get_columns("job_requirement_profiles")
            if column["name"] == "jd_version_id"
        )
        if nullable:
            with op.batch_alter_table("job_requirement_profiles") as batch:
                batch.alter_column(
                    "jd_version_id",
                    existing_type=sa.String(length=96),
                    nullable=False,
                )

    # 已有正式画像标为 ready；其余历史 JD 版本等待后续补偿入队。
    op.execute(
        """
        UPDATE job_versions
        SET profile_status = 'ready'
        WHERE EXISTS (
            SELECT 1
            FROM job_requirement_profiles
            WHERE job_requirement_profiles.job_id = job_versions.job_id
              AND job_requirement_profiles.jd_version_id = job_versions.jd_version_id
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    profile_columns = {
        column["name"] for column in inspector.get_columns("job_requirement_profiles")
    }
    if "jd_version_id" in profile_columns:
        with op.batch_alter_table("job_requirement_profiles") as batch:
            batch.alter_column(
                "jd_version_id",
                existing_type=sa.String(length=96),
                nullable=True,
            )
    version_columns = {column["name"] for column in inspector.get_columns("job_versions")}
    if "profile_status" in version_columns:
        with op.batch_alter_table("job_versions") as batch:
            batch.drop_index("ix_job_versions_profile_status")
            batch.drop_column("profile_completed_at")
            batch.drop_column("profile_started_at")
            batch.drop_column("profile_error_message")
            batch.drop_column("profile_status")
