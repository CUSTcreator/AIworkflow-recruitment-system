from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class JobCapabilityInput:
    application_id: str
    candidate_id: str
    job_id: str
    jd_text: str
    resume_profile: dict[str, Any]
    profile_version: str
    stage: str = "screening"
    business_hard_constraints: list[dict[str, Any]] = field(default_factory=list)
    business_group_weights: dict[str, float] = field(default_factory=dict)
    interview_evidence: list[dict[str, Any]] = field(default_factory=list)
    scoring_evidence: dict[str, Any] = field(default_factory=dict)
    job_profile: dict[str, Any] = field(default_factory=dict)
    preset_model: dict[str, Any] = field(default_factory=dict)
    assessment_units: list[dict[str, Any]] = field(default_factory=list)
    llm_config: dict[str, Any] = field(default_factory=dict)


class JobCapabilityValidationError(ValueError):
    pass
