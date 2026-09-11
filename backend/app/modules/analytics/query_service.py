from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Application, Department, User
from backend.app.modules.auth.public import AuthorizationService, ScopeService
from backend.app.modules.analytics.schemas import (
    AnalyticsReadModel,
    AnalyticsSummary,
    DepartmentAnalyticsItem,
    StageAnalyticsItem,
)


STATUS_LABELS = {
    "hard_screening_pending": "硬筛等待中",
    "hard_screening_running": "硬筛运行中",
    "hard_screening_review": "硬筛人工复核",
    "submitted": "已提交",
    "screening_running": "初步筛选运行中",
    "screening_failed": "初步筛选异常",
    "department_review": "部门初步筛选审核",
    "first_interview_planning": "一面题单确认",
    "first_interview_scheduled": "一面已排期",
    "first_interview_in_progress": "一面进行中",
    "first_interview_evaluation": "一面面评确认",
    "hr_second_review": "HR 二面审核",
    "second_interview_in_progress": "HR 二面进行中",
    "second_interview_evaluation": "HR 二面评价确认",
    "final_review": "最终决策",
    "offer_process": "已通过",
    "resume_replaced": "简历已被覆盖",
    "on_hold": "暂缓",
    "manual_review": "人工复核",
    "closed_rejected": "不通过",
    "closed_cancelled": "招聘取消",
}


class AnalyticsQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def overview(self, user: User) -> AnalyticsReadModel:
        """候选人统计与申请阶段统计分开计算。

        一个 Candidate 可以有多个 Application：候选人总数和部门规模必须去重；流程阶段、通过和
        拒绝仍是申请级指标，因此前端明确标注为“申请”。
        """
        query = (
            select(Application.candidate_id, Application.status, Department.name)
            .join(Department, Department.department_id == Application.department_id)
            .where(Application.deleted_at.is_(None))
        )
        scope = ScopeService(AuthorizationService(self.db)).data_scope(user)
        if scope.business_scope != "organization":
            query = query.where(Application.department_id == scope.department_id)

        rows = self.db.execute(query).all()
        stage_counts = Counter(str(row.status) for row in rows)
        department_candidates: dict[str, set[str]] = {}
        department_statuses: dict[str, Counter[str]] = {}
        candidate_ids: set[str] = set()
        for row in rows:
            department = str(row.name or "未分配部门")
            if row.candidate_id:
                candidate_id = str(row.candidate_id)
                candidate_ids.add(candidate_id)
                department_candidates.setdefault(department, set()).add(candidate_id)
            department_statuses.setdefault(department, Counter())[str(row.status)] += 1

        departments = [
            DepartmentAnalyticsItem(
                department=department,
                total=len(department_candidates.get(department, set())),
                finalReview=counts["final_review"],
                passed=counts["offer_process"],
                rejected=counts["closed_rejected"],
            )
            for department, counts in department_statuses.items()
        ]
        departments.sort(key=lambda item: (-item.total, item.department))
        stages = [
            StageAnalyticsItem(status=status, label=STATUS_LABELS.get(status, status), count=count)
            for status, count in stage_counts.items()
        ]
        stages.sort(key=lambda item: list(STATUS_LABELS).index(item.status) if item.status in STATUS_LABELS else len(STATUS_LABELS))
        return AnalyticsReadModel(
            summary=AnalyticsSummary(
                departmentCount=len(departments),
                candidateCount=len(candidate_ids),
                finalReviewCount=stage_counts["final_review"],
            ),
            departments=departments,
            stages=stages,
        )
