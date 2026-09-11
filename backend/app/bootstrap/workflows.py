"""应用启动时的工作流装配表：注册全部持久化任务类型及其业务处理器、失败收尾处理器和并发锁。"""

from __future__ import annotations

from sqlalchemy import select

from backend.app.infrastructure.workflow_runtime.runtime import WorkflowRuntime
from backend.app.models.entities import Application, JobDocumentImport, JobVersionRecord, ResumeSubmission
from backend.app.modules.assessment.workflows.hard_screening_workflow import build_hard_screening_spec
from backend.app.modules.assessment.workflows.scoring_workflow import build_scoring_spec
from backend.app.modules.assessment.workflows.transitions import (
    handle_hard_screening_blocked_transition,
    handle_hard_screening_transition,
    handle_screening_blocked_transition,
    handle_screening_transition,
)
from backend.app.modules.document_ingestion.workflows.job_document_import_workflow import build_job_document_import_spec
from backend.app.modules.document_ingestion.workflows.resume_document_import_workflow import build_resume_document_import_spec
from backend.app.modules.document_ingestion.workflows.transitions import (
    handle_job_document_blocked,
    handle_job_document_transition,
    handle_resume_document_blocked,
    handle_resume_document_transition,
)
from backend.app.modules.interviews.workflows.transitions import (
    handle_first_planning_blocked_transition,
    handle_first_planning_transition,
    handle_first_scoring_blocked_transition,
    handle_first_scoring_transition,
    handle_second_scoring_blocked_transition,
    handle_second_scoring_transition,
)
from backend.app.modules.interviews.workflows.first_interview_planning_workflow import build_first_interview_planning_spec
from backend.app.modules.interviews.workflows.post_first_scoring_workflow import build_post_first_scoring_spec
from backend.app.modules.interviews.workflows.post_second_scoring_workflow import build_post_second_scoring_spec
from backend.app.modules.jobs.workflows.job_profile_compilation_workflow import build_job_profile_compilation_spec
from backend.app.modules.jobs.workflows.transitions import handle_job_profile_blocked, handle_job_profile_transition
from backend.app.modules.candidates.workflows.candidate_routing_workflow import build_candidate_routing_spec
from backend.app.modules.candidates.workflows.transitions import (
    handle_candidate_routing_blocked,
    handle_candidate_routing_transition,
)


def _lock_application(db, subject_id: str) -> None:
    """为 Application 类任务获取行锁，串行化同一申请的状态改变。"""
    db.scalar(select(Application).where(Application.application_id == subject_id).with_for_update())


def _lock_job_document_import(db, subject_id: str) -> None:
    """锁定岗位导入业务任务；文件资产本身没有状态流转。"""
    db.scalar(select(JobDocumentImport).where(JobDocumentImport.job_document_import_id == subject_id).with_for_update())


def _lock_resume_submission(db, subject_id: str) -> None:
    """为同一次简历提交获取行锁，确保状态流转按顺序发生。"""
    db.scalar(select(ResumeSubmission).where(ResumeSubmission.resume_submission_id == subject_id).with_for_update())

def _lock_job_version(db, subject_id: str) -> None:
    """为同一版冻结 JD 获取行锁，避免重复发布或覆盖岗位画像。"""
    db.scalar(select(JobVersionRecord).where(JobVersionRecord.jd_version_id == subject_id).with_for_update())


def build_workflow_runtime() -> WorkflowRuntime:
    """注册系统内全部步骤式工作流。

    WorkflowRun 只保存 ``workflow_type`` 和定义版本；Worker 通过这里注册的
    WorkflowSpec 找到对应的 StepDefinition 顺序。每个业务工作流的编排都在其
    workflows 文件中，transition_handler 仅负责最终失败时同步领域状态。
    """
    runtime = WorkflowRuntime()
    # 新上传任务使用 v2：解析后先完成确认前要求分类；历史 v1 任务保留旧定义，
    # 避免部署后已有检查点无法恢复。Activity 本身不需要单独注册。
    runtime.register(build_job_document_import_spec(
        handle_job_document_transition,
        handle_job_document_blocked,
        definition_version=1,
        include_requirement_classification=False,
    ))
    runtime.register(build_job_document_import_spec(
        handle_job_document_transition,
        handle_job_document_blocked,
        definition_version=2,
        include_requirement_classification=True,
    ))
    runtime.register(build_resume_document_import_spec(
        handle_resume_document_transition,
        handle_resume_document_blocked,
    ))
    runtime.register(build_hard_screening_spec(handle_hard_screening_transition, handle_hard_screening_blocked_transition))
    runtime.register(build_scoring_spec(handle_screening_transition, handle_screening_blocked_transition))
    runtime.register(build_first_interview_planning_spec(
        handle_first_planning_transition,
        handle_first_planning_blocked_transition,
    ))
    runtime.register(build_post_first_scoring_spec(
        handle_first_scoring_transition,
        handle_first_scoring_blocked_transition,
    ))
    runtime.register(build_post_second_scoring_spec(
        handle_second_scoring_transition,
        handle_second_scoring_blocked_transition,
    ))
    runtime.register(build_candidate_routing_spec(handle_candidate_routing_transition, handle_candidate_routing_blocked))
    # 岗位画像使用步骤式恢复执行器：冻结版本 → 外部编译/发布 → 版本可用。
    runtime.register(build_job_profile_compilation_spec(
        handle_job_profile_transition,
        handle_job_profile_blocked,
    ))
    runtime.register_subject_locker("application", _lock_application)
    runtime.register_subject_locker("job_document_import", _lock_job_document_import)
    runtime.register_subject_locker("resume_submission", _lock_resume_submission)
    runtime.register_subject_locker("job_version", _lock_job_version)
    return runtime


_WORKFLOW_RUNTIME = build_workflow_runtime()


def get_workflow_runtime() -> WorkflowRuntime:
    """返回应用进程唯一的已装配运行时；Worker 与 Web 进程使用相同的类型清单。"""
    return _WORKFLOW_RUNTIME
