from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommonQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionId: str | None = Field(default=None, max_length=96)
    question: str = Field(min_length=1, max_length=2_000)
    evaluationPoints: list[str] = Field(default_factory=list, max_length=20)
    required: bool = True
    resultType: Literal["capability", "non_scoring"] = "capability"


class TemplateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    templateId: str | None = Field(default=None, max_length=64)
    questions: list[CommonQuestionInput] = Field(min_length=1, max_length=200)


class TemplateDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    questions: list[CommonQuestionInput] | None = Field(default=None, min_length=1, max_length=200)


class TemplatePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isDefault: bool = False


class TemplateStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isActive: bool


class JobTemplateBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templateId: str | None = Field(default=None, max_length=64)
