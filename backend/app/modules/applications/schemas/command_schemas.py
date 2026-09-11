from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EmptyActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobProfileRecoveryRequest(BaseModel):
    """V1 岗位来源恢复只允许重建冻结版本或明确采用岗位当前版本。"""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["reprocess_frozen", "adopt_current"] = "reprocess_frozen"


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["暂缓", "不推进", "人工复核", "补充验证"]


class FinalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["offer_process", "closed_rejected", "manual_review"]


class ApplicationCommandResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    application_id: str
    status: str
    message: str
    next_route: str | None = None
    workflow_run_id: str | None = None
    workflow_type: str | None = None
    run_status: Literal["pending", "queued", "running", "completed", "failed"] | None = None


class DepartmentOption(BaseModel):
    departmentId: str
    name: str
