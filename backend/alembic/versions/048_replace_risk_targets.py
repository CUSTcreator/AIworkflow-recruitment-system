"""replace risk persistence with structured interview targets

Revision ID: 048_replace_risk_targets
Revises: 047_remove_legacy_resume_state
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "048_replace_risk_targets"
down_revision = "047_remove_legacy_resume_state"
branch_labels = None
depends_on = None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _json_text(sql: str, *names: str) -> sa.TextClause:
    return sa.text(sql).bindparams(
        *(sa.bindparam(name, type_=sa.JSON()) for name in names)
    )

def upgrade() -> None:
    # 1. 初筛稳定业务字段从旧 payload 中拆出。
    with op.batch_alter_table("screening_assessments") as batch:
        batch.add_column(sa.Column("screening_assessment_id", sa.String(length=96), nullable=True))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("score_status", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("base_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("qualification_gate", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("job_capability_fit_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("resume_demonstrated_capability_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("resume_experience_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("education_background_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_bundle_ref", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("source_snapshot_hash", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("generation_mode", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("generated_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("screening_result_view", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("decision_overview", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("failure_details", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, application_id, payload, created_at FROM screening_assessments "
        "ORDER BY application_id, created_at, id"
    )).mappings().all()
    versions: dict[str, int] = {}
    for row in rows:
        payload = _mapping(row["payload"])
        application_id = str(row["application_id"])
        version = versions.get(application_id, 0) + 1
        versions[application_id] = version
        version_metadata = _mapping(payload.get("versionMetadata"))
        failure_details = {
            key: payload[key]
            for key in ("errors", "failureType", "failureReason", "error", "currentStage", "confidence")
            if key in payload
        }
        connection.execute(_json_text(
            "UPDATE screening_assessments SET "
            "screening_assessment_id=:assessment_id, version=:version, score_status=:score_status, "
            "base_score=:base_score, qualification_gate=:qualification_gate, "
            "job_capability_fit_score=:job_fit, resume_demonstrated_capability_score=:demonstrated, "
            "resume_experience_score=:experience, education_background_score=:education, summary=:summary, "
            "source_bundle_ref=:bundle_ref, source_snapshot_hash=:snapshot_hash, generation_mode=:generation_mode, "
            "generated_at=:generated_at, screening_result_view=:result_view, decision_overview=:overview, "
            "failure_details=:failure_details, updated_at=:updated_at WHERE id=:id"
        , "result_view", "overview", "failure_details"), {
            "id": row["id"],
            "assessment_id": str(payload.get("screeningAssessmentId") or f"SA_{application_id}_{version}"),
            "version": version,
            "score_status": str(payload.get("scoreStatus") or "pending"),
            "base_score": payload.get("baseScore"),
            "qualification_gate": str(payload.get("qualificationGate") or "unclear"),
            "job_fit": payload.get("jobCapabilityFitScore"),
            "demonstrated": payload.get("resumeDemonstratedCapabilityScore"),
            "experience": payload.get("resumeExperienceScore"),
            "education": payload.get("educationBackgroundScore"),
            "summary": str(payload.get("summary") or ""),
            "bundle_ref": payload.get("sourceBundleRef"),
            "snapshot_hash": payload.get("sourceSnapshotHash"),
            "generation_mode": payload.get("generationMode"),
            "generated_at": _timestamp(payload.get("generatedAt") or version_metadata.get("generatedAt")),
            "result_view": _mapping(payload.get("screeningResultView")),
            "overview": _mapping(payload.get("decisionOverview")),
            "failure_details": failure_details,
            "updated_at": row["created_at"],
        })

    with op.batch_alter_table("screening_assessments") as batch:
        batch.drop_column("payload")
        batch.create_index("ix_screening_assessments_assessment_id", ["screening_assessment_id"], unique=True)
        batch.create_index("ix_screening_assessments_score_status", ["score_status"], unique=False)
        batch.create_unique_constraint("uq_screening_assessment_application_version", ["application_id", "version"])
        batch.create_index("ix_screening_assessments_application_created", ["application_id", "created_at"], unique=False)

    # 2. InterviewTarget 成为唯一的跨阶段核验状态；将原 target payload 拆成明确字段。
    with op.batch_alter_table("interview_targets") as batch:
        batch.add_column(sa.Column("interview_target_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("purpose", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("target_type", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("target_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("title", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("verification_goal", sa.Text(), nullable=True))
        batch.add_column(sa.Column("trigger_code", sa.String(length=96), nullable=True))
        batch.add_column(sa.Column("status", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("stage_created", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("resolved_stage", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("resolution_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_result_ids", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("evidence_ids", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("attributes", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))

    target_rows = connection.execute(sa.text(
        "SELECT id, application_id, payload, created_at FROM interview_targets ORDER BY id"
    )).mappings().all()
    for row in target_rows:
        payload = _mapping(row["payload"])
        reserved = {
            "interview_target_id", "interviewTargetId", "application_id", "applicationId", "purpose",
            "target_type", "targetType", "target_id", "targetId", "title", "verification_goal",
            "verificationGoal", "trigger_code", "triggerCode", "status", "stage_created", "stageCreated",
            "resolved_stage", "resolvedStage", "resolution_note", "resolutionNote", "source_result_ids",
            "sourceResultIds", "evidence_ids", "evidenceIds",
        }
        connection.execute(_json_text(
            "UPDATE interview_targets SET interview_target_id=:target_key, purpose=:purpose, "
            "target_type=:target_type, target_id=:target_id, title=:title, verification_goal=:goal, "
            "trigger_code=:trigger_code, status=:status, stage_created=:stage_created, "
            "resolved_stage=:resolved_stage, resolution_note=:resolution_note, source_result_ids=:source_ids, "
            "evidence_ids=:evidence_ids, attributes=:attributes, updated_at=:updated_at WHERE id=:id"
        , "source_ids", "evidence_ids", "attributes"), {
            "id": row["id"],
            "target_key": str(payload.get("interview_target_id") or payload.get("interviewTargetId") or f"IT_LEGACY_{row['id']}"),
            "purpose": str(payload.get("purpose") or "verify_experience"),
            "target_type": str(payload.get("target_type") or payload.get("targetType") or "job_capability"),
            "target_id": str(payload.get("target_id") or payload.get("targetId") or ""),
            "title": str(payload.get("title") or payload.get("verification_goal") or payload.get("verificationGoal") or ""),
            "goal": str(payload.get("verification_goal") or payload.get("verificationGoal") or ""),
            "trigger_code": str(payload.get("trigger_code") or payload.get("triggerCode") or ""),
            "status": str(payload.get("status") or "open"),
            "stage_created": str(payload.get("stage_created") or payload.get("stageCreated") or "screening"),
            "resolved_stage": payload.get("resolved_stage") or payload.get("resolvedStage"),
            "resolution_note": payload.get("resolution_note") or payload.get("resolutionNote"),
            "source_ids": _list(payload.get("source_result_ids") or payload.get("sourceResultIds")),
            "evidence_ids": _list(payload.get("evidence_ids") or payload.get("evidenceIds")),
            "attributes": {key: value for key, value in payload.items() if key not in reserved},
            "updated_at": row["created_at"],
        })

    # 3. 历史风险只转为待核验目标，不再保留独立 Risk/RiskUpdate 状态。
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())
    if "risks" in existing_tables:
        risk_rows = connection.execute(sa.text(
            "SELECT id, application_id, payload, created_at FROM risks ORDER BY id"
        )).mappings().all()
        for row in risk_rows:
            payload = _mapping(row["payload"])
            source_id = str(payload.get("risk_id") or payload.get("riskId") or row["id"])
            target_key = f"IT_LEGACY_RISK_{source_id}"
            exists = connection.execute(sa.text(
                "SELECT 1 FROM interview_targets WHERE application_id=:application_id AND interview_target_id=:target_key"
            ), {"application_id": row["application_id"], "target_key": target_key}).scalar()
            if exists:
                continue
            title = str(payload.get("summary") or payload.get("title") or "需要核验的历史记录")
            connection.execute(_json_text(
                "INSERT INTO interview_targets (application_id, interview_target_id, purpose, target_type, target_id, "
                "title, verification_goal, trigger_code, status, stage_created, source_result_ids, evidence_ids, attributes, created_at, updated_at) "
                "VALUES (:application_id, :target_key, :purpose, :target_type, :target_id, :title, :goal, :trigger, "
                ":status, :stage, :source_ids, :evidence_ids, :attributes, :created_at, :updated_at)",
                "source_ids", "evidence_ids", "attributes",
            ), {
                "application_id": row["application_id"],
                "target_key": target_key,
                "purpose": "verify_fact",
                "target_type": "historical_conflict",
                "target_id": source_id,
                "title": title,
                "goal": str(payload.get("verification_need") or payload.get("recommendedInterviewAction") or title),
                "trigger": str(payload.get("risk_type") or payload.get("category") or "legacy_risk"),
                "status": str(payload.get("status") or "open"),
                "stage": str(payload.get("stage_created") or "legacy"),
                "source_ids": _list(payload.get("target_refs")),
                "evidence_ids": _list(payload.get("evidence_ids") or payload.get("sourceEvidenceIds")),
                "attributes": {"migrated_from": "risks", "legacy_risk_id": source_id},
                "created_at": row["created_at"],
                "updated_at": row["created_at"],
            })

    with op.batch_alter_table("interview_targets") as batch:
        batch.drop_column("payload")
        batch.create_index("ix_interview_targets_target_id", ["interview_target_id"], unique=False)
        batch.create_index("ix_interview_targets_status", ["status"], unique=False)
        batch.create_unique_constraint("uq_interview_target_application_target", ["application_id", "interview_target_id"])
        batch.create_index("ix_interview_targets_application_status", ["application_id", "status"], unique=False)

    if "risk_updates" in existing_tables:
        op.drop_table("risk_updates")
    if "risks" in existing_tables:
        op.drop_table("risks")


def downgrade() -> None:
    raise RuntimeError("048 删除了 Risk/RiskUpdate 历史状态，不支持自动降级。")