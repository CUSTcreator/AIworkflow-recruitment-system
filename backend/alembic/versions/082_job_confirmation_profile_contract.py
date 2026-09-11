"""将岗位确认与岗位画像从泛化 payload 收紧为具名字段合同。

Revision ID: 082_job_confirmation_profile_contract
Revises: 081_job_document_named_fields

本迁移会回填开发/历史记录后删除四个泛化列：job_drafts.payload、
jobs.payload、job_versions.snapshot、job_requirement_profiles.payload。
执行 upgrade 前必须确认目标数据库已完成备份；downgrade 不支持恢复已删除的旧列。
"""
from __future__ import annotations

import json
from typing import Any

from alembic import op
import sqlalchemy as sa

revision = "082_job_confirmation_profile_contract"
down_revision = "081_job_document_named_fields"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _add_columns() -> None:
    draft_columns = _columns("job_drafts")
    with op.batch_alter_table("job_drafts") as batch:
        if "preset_model_id" not in draft_columns:
            batch.add_column(sa.Column("preset_model_id", sa.String(length=64), nullable=False, server_default="engineering_experience"))
        if "preset_model_version" not in draft_columns:
            batch.add_column(sa.Column("preset_model_version", sa.String(length=32), nullable=False, server_default="1.0"))
        if "recommended_preset_model_id" not in draft_columns:
            batch.add_column(sa.Column("recommended_preset_model_id", sa.String(length=64), nullable=False, server_default="engineering_experience"))
        if "preset_model_selection_source" not in draft_columns:
            batch.add_column(sa.Column("preset_model_selection_source", sa.String(length=32), nullable=False, server_default="rule"))
        if "major_requirement_source" not in draft_columns:
            batch.add_column(sa.Column("major_requirement_source", sa.String(length=64), nullable=True))
        if "major_requirement_source_quote" not in draft_columns:
            batch.add_column(sa.Column("major_requirement_source_quote", sa.Text(), nullable=True))
        if "field_provenance_json" not in draft_columns:
            batch.add_column(sa.Column("field_provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        if "department_auto_created" not in draft_columns:
            batch.add_column(sa.Column("department_auto_created", sa.Boolean(), nullable=False, server_default=sa.false()))

    job_columns = _columns("jobs")
    with op.batch_alter_table("jobs") as batch:
        if "preset_model_id" not in job_columns:
            batch.add_column(sa.Column("preset_model_id", sa.String(length=64), nullable=False, server_default="engineering_experience"))
        if "preset_model_version" not in job_columns:
            batch.add_column(sa.Column("preset_model_version", sa.String(length=32), nullable=False, server_default="1.0"))

    version_columns = _columns("job_versions")
    with op.batch_alter_table("job_versions") as batch:
        if "frozen_schema_version" not in version_columns:
            batch.add_column(sa.Column("frozen_schema_version", sa.String(length=64), nullable=False, server_default="job_version_input_v1"))
        if "frozen_job_json" not in version_columns:
            batch.add_column(sa.Column("frozen_job_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        if "preset_model_id" not in version_columns:
            batch.add_column(sa.Column("preset_model_id", sa.String(length=64), nullable=False, server_default="engineering_experience"))
        if "preset_model_version" not in version_columns:
            batch.add_column(sa.Column("preset_model_version", sa.String(length=32), nullable=False, server_default="1.0"))
        if "job_capability_algorithm_version" not in version_columns:
            batch.add_column(sa.Column("job_capability_algorithm_version", sa.String(length=64), nullable=False, server_default="job_capability_v1.0"))

    profile_columns = _columns("job_requirement_profiles")
    with op.batch_alter_table("job_requirement_profiles") as batch:
        if "profile_schema_version" not in profile_columns:
            batch.add_column(sa.Column("profile_schema_version", sa.String(length=64), nullable=False, server_default="job_requirement_profile_v1"))
        if "algorithm_version" not in profile_columns:
            batch.add_column(sa.Column("algorithm_version", sa.String(length=64), nullable=False, server_default="job_capability_v1.0"))
        if "profile_json" not in profile_columns:
            batch.add_column(sa.Column("profile_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def _backfill_named_fields() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    drafts = sa.Table("job_drafts", metadata, autoload_with=bind)
    for row in bind.execute(sa.select(drafts)).mappings():
        payload = _json_dict(row.get("payload"))
        extraction = _json_dict(payload.get("extraction_meta"))
        bind.execute(
            drafts.update().where(drafts.c.job_draft_id == row["job_draft_id"]).values(
                preset_model_id=str(payload.get("preset_model_id") or row.get("preset_model_id") or "engineering_experience"),
                preset_model_version=str(payload.get("preset_model_version") or row.get("preset_model_version") or "1.0"),
                recommended_preset_model_id=str(payload.get("recommended_preset_model_id") or row.get("recommended_preset_model_id") or payload.get("preset_model_id") or "engineering_experience"),
                preset_model_selection_source=str(payload.get("selection_source") or row.get("preset_model_selection_source") or "rule"),
                major_requirement_source=payload.get("major_source") or row.get("major_requirement_source"),
                major_requirement_source_quote=payload.get("major_source_quote") or row.get("major_requirement_source_quote"),
                field_provenance_json=extraction or _json_dict(row.get("field_provenance_json")),
                department_auto_created=bool(payload.get("department_auto_created") or row.get("department_auto_created")),
            )
        )

    jobs = sa.Table("jobs", metadata, autoload_with=bind)
    for row in bind.execute(sa.select(jobs)).mappings():
        payload = _json_dict(row.get("payload"))
        bind.execute(
            jobs.update().where(jobs.c.job_id == row["job_id"]).values(
                preset_model_id=str(payload.get("preset_model_id") or row.get("preset_model_id") or "engineering_experience"),
                preset_model_version=str(payload.get("preset_model_version") or row.get("preset_model_version") or "1.0"),
            )
        )

    versions = sa.Table("job_versions", metadata, autoload_with=bind)
    frozen_keys = {"title", "department_id", "headcount", "source_document_id", "responsibilities", "qualifications", "education_requirement", "major_requirement"}
    for row in bind.execute(sa.select(versions)).mappings():
        snapshot = _json_dict(row.get("snapshot"))
        frozen = {key: snapshot.get(key) for key in frozen_keys if key in snapshot}
        bind.execute(
            versions.update().where(versions.c.jd_version_id == row["jd_version_id"]).values(
                frozen_job_json=frozen or _json_dict(row.get("frozen_job_json")),
                preset_model_id=str(snapshot.get("preset_model_id") or row.get("preset_model_id") or "engineering_experience"),
                preset_model_version=str(snapshot.get("preset_model_version") or row.get("preset_model_version") or "1.0"),
                job_capability_algorithm_version=str(snapshot.get("job_capability_algorithm_version") or row.get("job_capability_algorithm_version") or "job_capability_v1.0"),
            )
        )

    profiles = sa.Table("job_requirement_profiles", metadata, autoload_with=bind)
    profile_keys = {"jd_units", "assessment_units", "job_capabilities", "qualification_constraints", "degraded"}
    for row in bind.execute(sa.select(profiles)).mappings():
        payload = _json_dict(row.get("payload"))
        profile_json = {key: payload.get(key) for key in profile_keys if key in payload}
        versions_payload = _json_dict(payload.get("versions"))
        bind.execute(
            profiles.update().where(profiles.c.job_profile_id == row["job_profile_id"]).values(
                profile_schema_version=str(payload.get("schema_version") or row.get("profile_schema_version") or "job_requirement_profile_v1"),
                algorithm_version=str(versions_payload.get("algorithm_process_version") or row.get("algorithm_version") or "job_capability_v1.0"),
                profile_json=profile_json or _json_dict(row.get("profile_json")),
            )
        )


def _drop_legacy_columns() -> None:
    for table, column in (
        ("job_drafts", "payload"),
        ("jobs", "payload"),
        ("job_versions", "snapshot"),
        ("job_requirement_profiles", "payload"),
    ):
        if column in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)


def upgrade() -> None:
    _add_columns()
    _backfill_named_fields()
    _drop_legacy_columns()


def downgrade() -> None:
    raise RuntimeError("082_job_confirmation_profile_contract 不支持降级：旧泛化列已被有意删除")