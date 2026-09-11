from pydantic import BaseModel, Field


class AnalyticsSummary(BaseModel):
    departmentCount: int = 0
    candidateCount: int = 0
    finalReviewCount: int = 0


class DepartmentAnalyticsItem(BaseModel):
    department: str
    total: int = 0
    finalReview: int = 0
    passed: int = 0
    rejected: int = 0


class StageAnalyticsItem(BaseModel):
    status: str
    label: str
    count: int = 0


class AnalyticsReadModel(BaseModel):
    summary: AnalyticsSummary = Field(default_factory=AnalyticsSummary)
    departments: list[DepartmentAnalyticsItem] = Field(default_factory=list)
    stages: list[StageAnalyticsItem] = Field(default_factory=list)
