"""add job profile gating and profile revisions

Revision ID: 070_job_profile_gating
Revises: 069_enforce_runtime_not_null
Create Date: 2026-08-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "070_job_profile_gating"
down_revision = "069_enforce_runtime_not_null"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _unique_names(table_name: str) -> set[str]:
    return {
        str(item.get("name"))
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
        if item.get("name")
    }


def upgrade() -> None:
    """为“JD 可分发、画像完成后才评分”建立冻结关系和历史兼容回填。"""
    bind = op.get_bind()

    with op.batch_alter_table("job_versions") as batch:
        columns = _columns("job_versions")
        if "active_job_profile_id" not in columns:
            batch.add_column(sa.Column("active_job_profile_id", sa.String(length=96), nullable=True))
            batch.create_foreign_key(
                "fk_job_versions_active_job_profile_id",
                "job_requirement_profiles",
                ["active_job_profile_id"],
                ["job_profile_id"],
            )
            batch.create_index("ix_job_versions_active_job_profile_id", ["active_job_profile_id"])
        if "profile_workflow_run_id" not in columns:
            batch.add_column(sa.Column("profile_workflow_run_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_job_versions_profile_workflow_run_id",
                "workflow_runs",
                ["profile_workflow_run_id"],
                ["workflow_run_id"],
            )
            batch.create_index("ix_job_versions_profile_workflow_run_id", ["profile_workflow_run_id"])

    with op.batch_alter_table("applications") as batch:
        columns = _columns("applications")
        if "job_profile_id" not in columns:
            batch.add_column(sa.Column("job_profile_id", sa.String(length=96), nullable=True))
            batch.create_foreign_key(
                "fk_applications_job_profile_id",
                "job_requirement_profiles",
                ["job_profile_id"],
                ["job_profile_id"],
            )
            batch.create_index("ix_applications_job_profile_id", ["job_profile_id"])
        batch.alter_column(
            "assigned_first_interviewer",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch.alter_column(
            "assigned_hr",
            existing_type=sa.String(length=64),
            nullable=True,
        )

    profile_columns = _columns("job_requirement_profiles")
    with op.batch_alter_table("job_requirement_profiles") as batch:
        if "revision" not in profile_columns:
            batch.add_column(
                sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
            )

    # 历史每份 JD 只有一份画像：把它回填为 revision=1，并固定为该版本当前有效画像。
    bind.execute(sa.text("""
        UPDATE job_versions
        SET active_job_profile_id = (
            SELECT p.job_profile_id
            FROM job_requirement_profiles AS p
            WHERE p.jd_version_id = job_versions.jd_version_id
            ORDER BY p.created_at DESC
            LIMIT 1
        )
        WHERE active_job_profile_id IS NULL
    """))
    bind.execute(sa.text("""
        UPDATE applications
        SET job_profile_id = (
            SELECT p.job_profile_id
            FROM job_requirement_profiles AS p
            WHERE p.job_id = applications.job_id
              AND p.jd_version_id = applications.jd_version_id
            ORDER BY p.created_at DESC
            LIMIT 1
        )
        WHERE job_profile_id IS NULL
    """))

    # 旧导入逻辑把“尚未配置面试人员”的 Job 错写为 closed。它们的 payload 已明确标记
    # adminSetupRequired；迁移仅转换这些可识别的历史行，真实已关闭岗位保持 closed。
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("""
            UPDATE jobs
            SET status = 'setup_pending', closed_at = NULL
            WHERE status = 'closed'
              AND COALESCE(payload ->> 'adminSetupRequired', 'false') = 'true'
        """))
    else:
        bind.execute(sa.text("""
            UPDATE jobs
            SET status = 'setup_pending', closed_at = NULL
            WHERE status = 'closed'
              AND payload LIKE '%adminSetupRequired%'
        """))

    # 旧唯一约束阻止同一 JD 的画像重新生成；新的约束以冻结 JD + revision 为边界。
    unique_names = _unique_names("job_requirement_profiles")
    with op.batch_alter_table("job_requirement_profiles") as batch:
        for name in (
            "uq_job_requirement_profile_job_version",
            "uq_job_requirement_profile_job_source",
            "uq_job_requirement_profile_source_version",
        ):
            if name in unique_names:
                batch.drop_constraint(name, type_="unique")
        if "uq_job_requirement_profile_version_revision" not in unique_names:
            batch.create_unique_constraint(
                "uq_job_requirement_profile_version_revision",
                ["jd_version_id", "revision"],
            )


def downgrade() -> None:
    """撤回新关系；历史画像不删除，但重新生成的多 revision 将无法继续写入旧约束。"""
    with op.batch_alter_table("job_requirement_profiles") as batch:
        unique_names = _unique_names("job_requirement_profiles")
        if "uq_job_requirement_profile_version_revision" in unique_names:
            batch.drop_constraint("uq_job_requirement_profile_version_revision", type_="unique")
        batch.create_unique_constraint(
            "uq_job_requirement_profile_job_version",
            ["job_id", "version"],
        )
        batch.create_unique_constraint(
            "uq_job_requirement_profile_job_source",
            ["job_id", "source_sha256"],
        )
        if "revision" in _columns("job_requirement_profiles"):
            batch.drop_column("revision")

    with op.batch_alter_table("applications") as batch:
        if "job_profile_id" in _columns("applications"):
            batch.drop_index("ix_applications_job_profile_id")
            batch.drop_constraint("fk_applications_job_profile_id", type_="foreignkey")
            batch.drop_column("job_profile_id")
        batch.alter_column("assigned_first_interviewer", existing_type=sa.String(length=64), nullable=False)
        batch.alter_column("assigned_hr", existing_type=sa.String(length=64), nullable=False)

    with op.batch_alter_table("job_versions") as batch:
        if "profile_workflow_run_id" in _columns("job_versions"):
            batch.drop_index("ix_job_versions_profile_workflow_run_id")
            batch.drop_constraint("fk_job_versions_profile_workflow_run_id", type_="foreignkey")
            batch.drop_column("profile_workflow_run_id")
        if "active_job_profile_id" in _columns("job_versions"):
            batch.drop_index("ix_job_versions_active_job_profile_id")
            batch.drop_constraint("fk_job_versions_active_job_profile_id", type_="foreignkey")
            batch.drop_column("active_job_profile_id")
