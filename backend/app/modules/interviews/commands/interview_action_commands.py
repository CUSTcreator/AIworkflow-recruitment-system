"""Interview 的同步写入命令编排与异步任务入队。"""

from typing import Any, Callable

from sqlalchemy.orm import Session

from backend.app.models.entities import User
from backend.app.modules.applications.public import ApplicationCommandExecutor, application_main_route
from backend.app.modules.interviews.commands.interview_workflow_request_commands import InterviewWorkflowRequestCommands
from backend.app.modules.interviews.commands.interview_lifecycle_commands import InterviewLifecycleCommands
from backend.app.modules.interviews.services.first_interview_planning_service import FirstInterviewPlanningService


class InterviewActionCommands:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def payload(body: Any | None) -> dict[str, Any]:
        return body.model_dump(exclude_unset=True, mode="json") if body is not None else {}

    def execute(self, *, user: User, idempotency_key: str, application_id: str, action: str, body: dict[str, Any], handler: Callable[..., dict[str, Any]]) -> dict[str, Any]:
        response = ApplicationCommandExecutor(self.db).execute(user=user, idempotency_key=idempotency_key, action=action, application_id=application_id, body=body, handler=handler)
        response.setdefault("next_route", application_main_route(str(response.get("application_id") or application_id), str(response.get("status") or "")))
        return response

    def save_first_guide_draft(self, *, user: User, idempotency_key: str, application_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.execute(user=user, idempotency_key=idempotency_key, application_id=application_id, action="confirm_first_guide", body=body, handler=FirstInterviewPlanningService(self.db).save_draft)

    def approve_first_interview(self, *, user: User, idempotency_key: str, application_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """在同一命令事务中批准一面并创建题单规划任务。"""
        lifecycle = InterviewLifecycleCommands(self.db)
        workflows = InterviewWorkflowRequestCommands(self.db)

        def handler(actor: User, app, request: dict[str, Any]) -> dict[str, Any]:
            response = lifecycle.approve_first_interview(actor, app, request)
            planning, _ = workflows.enqueue_in_transaction(
                app=app,
                user=actor,
                action="run_first_interview_planning",
                workflow_type="first_interview_planning_workflow",
                body={},
            )
            response.update({key: planning.get(key) for key in ("workflow_run_id", "workflow_type", "run_status")})
            return response

        return self.execute(
            user=user,
            idempotency_key=idempotency_key,
            application_id=application_id,
            action="approve_first_interview",
            body=body,
            handler=handler,
        )

    def complete_first_interview(self, *, user: User, idempotency_key: str, application_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """一面结果无论通过或拒绝都先固化原始记录，再由 V2 Workflow 统一处理。"""
        return self.enqueue(user=user, idempotency_key=idempotency_key, application_id=application_id, action="complete_first_interview", workflow_type="post_first_scoring_workflow", body=body)

    def complete_second_interview(self, *, user: User, idempotency_key: str, application_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """二面结果无论通过或拒绝都先固化原始记录，再由 V3 Workflow 统一处理。"""
        return self.enqueue(user=user, idempotency_key=idempotency_key, application_id=application_id, action="complete_second_interview", workflow_type="post_second_scoring_workflow", body=body)

    def save_first_progress(self, *, user: User, idempotency_key: str, application_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.execute(user=user, idempotency_key=idempotency_key, application_id=application_id, action="save_first_interview_progress", body=body, handler=InterviewLifecycleCommands(self.db).save_first_interview_progress)

    def save_second_progress(self, *, user: User, idempotency_key: str, application_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.execute(user=user, idempotency_key=idempotency_key, application_id=application_id, action="save_second_interview_progress", body=body, handler=InterviewLifecycleCommands(self.db).save_second_interview_progress)


    def execute_action(self, *, user: User, idempotency_key: str, application_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "confirm_first_guide": FirstInterviewPlanningService(self.db).confirm,
            "continue_first_interview_manually": FirstInterviewPlanningService(self.db).continue_manually,
            "start_first_interview": InterviewLifecycleCommands(self.db).start_first_interview,
            "finish_first_interview": InterviewLifecycleCommands(self.db).finish_first_interview,
            "approve_second_interview": InterviewLifecycleCommands(self.db).approve_second_interview,
            "start_second_interview": InterviewLifecycleCommands(self.db).start_second_interview,
            "finish_second_interview": InterviewLifecycleCommands(self.db).finish_second_interview,
        }
        return self.execute(user=user, idempotency_key=idempotency_key, application_id=application_id, action=action, body=body, handler=handlers[action])
    def enqueue(self, *, user: User, idempotency_key: str, application_id: str, action: str, workflow_type: str, body: dict[str, Any]) -> dict[str, Any]:
        response, _ = InterviewWorkflowRequestCommands(self.db).accept(user=user, application_id=application_id, idempotency_key=idempotency_key, action=action, workflow_type=workflow_type, body=body)
        response["next_route"] = "/" if workflow_type.startswith("post_") else application_main_route(application_id, str(response.get("status") or ""))
        return response






