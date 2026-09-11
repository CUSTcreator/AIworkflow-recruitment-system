"""Document ingestion 的 HTTP 写入请求 Schema。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfirmedHardScreeningRuleInput(BaseModel):
    """岗位导入确认页中由用户最终确认的单条硬筛条件。"""

    criterion_type: Literal[
        "minimum_degree",
        "highest_education_status",
        "highest_education_graduation_year",
        "minimum_experience_years",
        "project_experience",
        "required_skill",
        "certification",
        "custom",
    ]
    name: str = Field(min_length=1, max_length=200)
    expected_value: str | float
    description: str = Field(default="", max_length=2_000)
    enabled: bool = True


class JobDraftPatch(BaseModel):
    preset_model_id: Literal["engineering_experience", "general_professional_experience"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=128)
    headcount: int | None = Field(default=None, ge=1, le=100000)
    responsibilities: list[str] | None = None
    qualifications: list[str] | None = None
    education_requirement: str | None = Field(default=None, max_length=255)
    major_requirement: str | None = Field(default=None, max_length=500)
    department_id: str | None = Field(default=None, min_length=1, max_length=64)
    resolution: Literal["create", "overwrite", "skip", "keep"] | None = None
    # 用户可在确认页先保存再确认；硬筛编辑必须随草稿持久化，不能只留在浏览器内存中。
    hard_screening_rules: list[ConfirmedHardScreeningRuleInput] | None = Field(
        default=None,
        max_length=50,
    )


class JobDraftConfirmationInput(BaseModel):
    draft_id: str = Field(min_length=1, max_length=64)
    hard_screening_rules: list[ConfirmedHardScreeningRuleInput] = Field(
        default_factory=list,
        max_length=50,
    )


class ConfirmJobDraftsRequest(BaseModel):
    # 硬筛条件和岗位草稿在同一次命令中确认；岗位画像不得再要求二次确认。
    drafts: list[JobDraftConfirmationInput] | None = Field(default=None, max_length=500)
    draft_ids: list[str] | None = None


class JobDocumentRepairRequest(BaseModel):
    """用户为岗位导入提供的最小人工修复输入。

    表头修复使用列号（相对于指定工作表），字段修复按行引用提交字段值。
    工作流会再次校验这些值，接口不直接修改解析结果或草稿。
    """

    action: Literal["review_job_headers", "review_job_fields"]
    sheet_name: str | None = Field(default=None, min_length=1, max_length=255)
    header_row_index: int | None = Field(default=None, ge=0, le=999)
    header_mapping: dict[str, int] | None = None
    fields: dict[str, dict[str, Any]] | None = None
