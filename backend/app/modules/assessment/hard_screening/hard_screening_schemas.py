from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class HardScreeningConditionInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    selected_values: list[str] | None = Field(default=None, validation_alias=AliasChoices("selected_values", "selectedValues"), max_length=100)
    min: float | None = None
    max: float | None = None
    text: str | None = Field(default=None, max_length=2_000)


class HardScreeningRuleInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    rule_id: str | None = Field(default=None, max_length=96, validation_alias=AliasChoices("rule_id", "ruleId"))
    criterion_type: Literal["minimum_degree", "highest_education_status", "highest_education_graduation_year", "minimum_experience_years", "project_experience", "required_skill", "certification", "custom"] | None = Field(default=None, validation_alias=AliasChoices("criterion_type", "criterionType"))
    name: str = Field(min_length=1, max_length=200)
    criterion_id: str | None = Field(default=None, max_length=96, validation_alias=AliasChoices("criterion_id", "criterionId"))
    value_mode: Literal["select", "number", "text"] | None = Field(default=None, validation_alias=AliasChoices("value_mode", "valueMode"))
    evaluation_binding: str | None = Field(default=None, max_length=96, validation_alias=AliasChoices("evaluation_binding", "evaluationBinding"))
    condition: HardScreeningConditionInput | None = None
    source_scope: Literal["full_resume", "education", "skills", "work_experience", "project_experience", "certifications", "awards"] | None = Field(default=None, validation_alias=AliasChoices("source_scope", "sourceScope"))
    operator: Literal["exists", "contains_any", "contains_all", "not_contains_any", "degree_at_least", "education_status_is", "year_between", "years_at_least", "years_between", "semantic_match"] | None = None
    expected_value: str | float | list[str] | None = Field(default=None, validation_alias=AliasChoices("expected_value", "expectedValue"))
    description: str = Field(default="", max_length=2_000)
    enabled: bool = True


class HardScreeningPolicyWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    rules: list[HardScreeningRuleInput] = Field(default_factory=list, max_length=50)


class HardScreeningReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["pass", "reject"]
    reason: str = Field(min_length=1, max_length=5_000)
