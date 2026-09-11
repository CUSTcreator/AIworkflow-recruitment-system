"""Assessment 的评分请求命令与工作流访问校验。"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.models.entities import User, WorkflowRun
from backend.app.modules.applications.public import ApplicationAccessService
from backend.app.modules.assessment.commands.scoring_request_commands import ScoringRequestCommands
from backend.app.modules.assessment.queries.read_models import ScreeningQueryService
from backend.app.modules.auth.public import assert_business_action
from backend.app.modules.document_ingestion.public import ResumeDocumentService


class AssessmentActionCommands:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def payload(body: BaseModel | None) -> dict[str, Any]:
        return body.model_dump(exclude_unset=True, mode="json") if body else {}

    def workflow_status(self, workflow_run_id: str, user: User) -> dict:
        run = self.db.get(WorkflowRun, workflow_run_id)
        if run is None:
            return ScreeningQueryService(self.db).workflow_run_status(workflow_run_id)
        if run.application_id:
            ApplicationAccessService(self.db).get_visible(user, run.application_id)
        elif run.subject_type == "resume_submission" and run.subject_id:
            ResumeDocumentService(self.db).get_submission(run.subject_id, user)
        else:
            assert_business_action(self.db, user, "job_document.upload", require_organization_scope=True)
        return ScreeningQueryService(self.db).workflow_run_status(workflow_run_id)

    def request_scoring(self, application_id: str, user: User, idempotency_key: str, body: BaseModel | None) -> dict:
        response, _ = ScoringRequestCommands(self.db).accept_scoring_request(user=user, application_id=application_id, idempotency_key=idempotency_key, body=self.payload(body))
        return response

    def rebuild_screening_assessment(
        self, application_id: str, user: User, idempotency_key: str,
    ) -> dict:
        return ScoringRequestCommands(self.db).accept_screening_rebuild(
            user=user,
            application_id=application_id,
            idempotency_key=idempotency_key,
        )



