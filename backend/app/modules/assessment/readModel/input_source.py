from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import ApplicationAssessmentVersion, InterviewParseResultRecord
from backend.app.modules.interviews.public import interview_parse_result_view
from backend.app.modules.applications.public import ApplicationReadModelSource, ApplicationReadSource
from backend.app.shared.errors import BusinessError


@dataclass(frozen=True, slots=True)
class AssessmentInputBundle:
    source: ApplicationReadSource
    score_snapshot: dict[str, Any]
    capability_profile: dict[str, Any]
    interview_parse_results: list[dict[str, Any]]
    resume_profile: dict[str, Any]
    job_profile: dict[str, Any]
    evidence_records: list[dict[str, Any]]
    source_mode: str

    @property
    def resume_evidence_spans(self) -> list[dict[str, Any]]:
        return list(self.resume_profile.get("candidate_spans") or [])

    @property
    def experience_units(self) -> list[dict[str, Any]]:
        return list(self.resume_profile.get("experience_units") or [])


class AssessmentInputSource:
    """Loads canonical versioned inputs and isolates historical-data adaptation."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.read_source = ApplicationReadModelSource(db)

    def load(
        self,
        application_id: str,
        *,
        score_stage: str | None = None,
    ) -> AssessmentInputBundle:
        source = self.read_source.load(application_id)
        # AAV 的唯一业务阶段枚举：screening / after_first_interview / after_second_interview。
        requested_stage = score_stage
        version = (
            source.assessment_versions.get(requested_stage)
            if requested_stage
            else source.current_assessment_version
        )
        if version is not None:
            core = dict(version.core_result_json or {})
            resume_profile = dict(source.resume_profile.profile_data or {}) if source.resume_profile else {}
            job_profile = dict(source.job_profile.profile_json or {}) if source.job_profile else {}
            parse_results: list[dict[str, Any]] = []
            if version.source_interview_parse_result_id:
                record = self.db.get(InterviewParseResultRecord, version.source_interview_parse_result_id)
                if record is not None:
                    parse_results.append(interview_parse_result_view(record))
            return AssessmentInputBundle(
                source=source,
                score_snapshot=dict(core.get("score_result") or {}),
                capability_profile=dict(core.get("capability_graph") or {}),
                interview_parse_results=parse_results,
                resume_profile=resume_profile,
                job_profile=job_profile,
                evidence_records=_evidence_records(resume_profile),
                source_mode="application_assessment_version",
            )
        score_row = (
            source.score_snapshots.get(score_stage)
            if score_stage is not None
            else source.current_score_snapshot
        )
        if all(
            (
                source.capability_profile,
                source.resume_profile,
                source.job_profile,
            )
        ):
            resume_payload = dict(source.resume_profile.profile_data or {})
            return AssessmentInputBundle(
                source=source,
                score_snapshot=_score_payload(source, score_row),
                capability_profile=dict(source.capability_profile.capability_json or {}),
                interview_parse_results=self._interview_parse_lineage(
                    source.capability_profile
                ),
                resume_profile=resume_payload,
                job_profile=dict(source.job_profile.profile_json or {}),
                evidence_records=_evidence_records(resume_payload),
                source_mode="versioned_records",
            )
        return self._load_historical(source, score_row)

    def _load_historical(
        self,
        source: ApplicationReadSource,
        score_row,
    ) -> AssessmentInputBundle:
        artifact = self.read_source.latest_artifact(
            source.application.application_id,
            "screening_result",
        )
        result = artifact.artifact_json if artifact and artifact.artifact_json else {}
        bundle = result.get("analysis_bundle") if isinstance(result, dict) else None
        if not isinstance(bundle, dict):
            raise BusinessError(
                "assessment_source_incomplete",
                "候选人的能力评估主数据不完整，请重新运行初步筛选",
                status_code=409,
            )
        resume_profile = dict(bundle.get("resume_profile") or {})
        return AssessmentInputBundle(
            source=source,
            score_snapshot=_score_payload(source, score_row),
            capability_profile=dict(bundle.get("candidate_capability_profile") or {}),
            interview_parse_results=[],
            resume_profile=resume_profile,
            job_profile=dict(bundle.get("job_profile") or {}),
            evidence_records=list(
                bundle.get("all_evidence_references")
                or _evidence_records(resume_profile)
            ),
            source_mode="historical_artifact",
        )

def _score_payload(source: ApplicationReadSource, score_row) -> dict[str, Any]:
    if score_row is not None:
        return dict(score_row.result_json or {})
    if source.assessment_payload:
        return dict(source.assessment_payload)
    raise BusinessError(
        "score_snapshot_missing",
        "候选人的评分快照不存在，请重新运行初步筛选",
        status_code=409,
    )


def _evidence_records(resume_profile: dict[str, Any]) -> list[dict[str, Any]]:
    from recruitment_ai_core.screening_scoring.profile_evidence import (
        nested_source_bullets,
        nested_work_units,
    )

    records: list[dict[str, Any]] = []
    for item in nested_source_bullets(resume_profile):
        records.append(
            {
                "evidence_id": item.get("source_bullet_id"),
                "evidence_type": "source_bullet",
                "raw_text": item.get("raw_text", ""),
                "source_line_start": item.get("source_line_start"),
                "source_line_end": item.get("source_line_end"),
                "source_block_ids": item.get("source_block_ids", []),
                "work_unit_ids": item.get("work_unit_ids", []),
            }
        )
    for item in nested_work_units(resume_profile):
        records.append(
            {
                "evidence_id": item.get("work_unit_id"),
                "evidence_type": "scorable_work_unit",
                "raw_text": item.get("raw_text", ""),
                "source_line_start": item.get("source_line_start"),
                "source_line_end": item.get("source_line_end"),
                "source_bullet_id": item.get("source_bullet_id"),
            }
        )
    return records
