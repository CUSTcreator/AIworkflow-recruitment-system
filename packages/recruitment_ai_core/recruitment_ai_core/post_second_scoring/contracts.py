from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PostSecondScoringConfig:
    minimum_meaningful_note_chars: int = 12


@dataclass(slots=True)
class PostSecondScoringInput:
    application_id: str
    candidate_id: str = ""
    job_id: str = ""
    job_title: str = ""
    job_summary: str | None = None
    after_first_score_snapshot: dict[str, Any] = field(default_factory=dict)
    candidate_capability_profile: dict[str, Any] = field(default_factory=dict)
    job_profile: dict[str, Any] = field(default_factory=dict)
    resume_profile: dict[str, Any] = field(default_factory=dict)
    previous_interview_parse_results: list[dict[str, Any]] = field(default_factory=list)
    previous_evidence_records: list[dict[str, Any]] = field(default_factory=list)
    current_risks: list[dict[str, Any]] = field(default_factory=list)
    interview_targets: list[dict[str, Any]] = field(default_factory=list)
    previous_decision_summary: dict[str, Any] = field(default_factory=dict)
    hr_second_review_package: dict[str, Any] = field(default_factory=dict)
    hr_raw_notes: list[dict[str, Any]] = field(default_factory=list)
    leader_raw_notes: list[dict[str, Any]] = field(default_factory=list)
    hr_final_assessment: dict[str, Any] = field(default_factory=dict)
    leader_final_assessment: dict[str, Any] = field(default_factory=dict)
    first_interview_evidence_units: list[dict[str, Any]] = field(default_factory=list)
    first_risk_updates: list[dict[str, Any]] = field(default_factory=list)
    internal_first_risk_updates: list[dict[str, Any]] = field(default_factory=list)
    capability_updates_after_first: list[dict[str, Any]] = field(default_factory=list)
    transferability_updates_after_first: list[dict[str, Any]] = field(default_factory=list)
    project_depth_updates_after_first: list[dict[str, Any]] = field(default_factory=list)
    screening_score_snapshot: dict[str, Any] = field(default_factory=dict)
    application_context: dict[str, Any] | None = None
    candidate_context: dict[str, Any] | None = None
    job_context: dict[str, Any] | None = None
    scoring_config: PostSecondScoringConfig = field(default_factory=PostSecondScoringConfig)
    metadata: dict[str, Any] = field(default_factory=dict)

