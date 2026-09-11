from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    Department,
    Job,
    ResumeSubmission,
    RoleDefinition,
    SourceDocument,
    User,
    WorkflowRun,
)


class SystemAdminRepository:
    """Entity lookup boundary shared by system-administration services."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def user(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def role(self, role_id: str) -> RoleDefinition | None:
        return self.db.get(RoleDefinition, role_id)

    def department(self, department_id: str) -> Department | None:
        return self.db.get(Department, department_id)

    def job(self, job_id: str) -> Job | None:
        return self.db.get(Job, job_id)

    def workflow(self, run_id: str) -> WorkflowRun | None:
        return self.db.get(WorkflowRun, run_id)

    def application(self, application_id: str) -> Application | None:
        return self.db.get(Application, application_id)

    def source_document(self, document_id: str) -> SourceDocument | None:
        return self.db.get(SourceDocument, document_id)

    def resume_submission(self, submission_id: str) -> ResumeSubmission | None:
        return self.db.get(ResumeSubmission, submission_id)