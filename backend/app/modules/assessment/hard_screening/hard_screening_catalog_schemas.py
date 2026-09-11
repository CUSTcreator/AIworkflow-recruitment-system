from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HardScreeningCriterionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=96)
    value_mode: Literal["select", "number", "text"] = Field(alias="valueMode")
    allowed_values: list[str] = Field(default_factory=list, alias="allowedValues", max_length=100)
    evaluation_binding: Literal[
        "highest_degree", "highest_education_status",
        "highest_education_graduation_year", "relevant_experience_years", "project_experience_semantic",
        "skills_semantic", "certifications_semantic", "full_resume_semantic",
    ] = Field(default="full_resume_semantic", alias="evaluationBinding")
    enabled: bool = True
    sort_order: int = Field(default=999, alias="sortOrder", ge=0, le=9999)


class HardScreeningCriterionUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    value_mode: Literal["select", "number", "text"] | None = Field(default=None, alias="valueMode")
    allowed_values: list[str] | None = Field(default=None, alias="allowedValues", max_length=100)
    evaluation_binding: Literal[
        "highest_degree", "highest_education_status",
        "highest_education_graduation_year", "relevant_experience_years", "project_experience_semantic",
        "skills_semantic", "certifications_semantic", "full_resume_semantic",
    ] | None = Field(default=None, alias="evaluationBinding")
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, alias="sortOrder", ge=0, le=9999)
