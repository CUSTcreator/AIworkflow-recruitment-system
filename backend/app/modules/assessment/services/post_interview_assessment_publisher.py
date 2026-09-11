"""V2/V3 唯一正式发布事务。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Mapping

from sqlalchemy import select

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    Interview,
    InterviewParseResultRecord,
    InterviewTarget,
    User,
)
from backend.app.modules.assessment.domain.assessment_version import (
    AssessmentCoreResult,
    AssessmentPresentationResult,
    AssessmentRuleResult,
    InterviewTargetUpdate,
)
from backend.app.modules.assessment.services.source_service import AssessmentSource
from backend.app.modules.assessment.services.topology_persistence import (
    V1TopologyPersistenceService,
)
from backend.app.modules.applications.application_recovery import (
    clear_application_recovery_for_prefix,
)
from backend.app.modules.tasks.public import TaskWriteService
from backend.app.infrastructure.database.locking import lock_business_resources
from recruitment_ai_core.incremental_scoring import IncrementalScoringResult
from recruitment_ai_core.interview_evaluation import InterviewParseDraft


_POST_INTERVIEW_SCORING_CONTRACT_VERSION = "anchor_matrix_v2"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _publication_key(
    source: AssessmentSource, *, recalculation_request_id: str = ""
) -> str:
    """生成面后正式发布的业务幂等键。

    StepRunner 的幂等键只保证同一 WorkflowRun 不重复执行；这里把冻结来源和业务
    重算请求共同固定下来，保证同一次发布重试只产生一个 V2/V3。
    """
    # 版本链上的 Snapshot 是稳定业务身份；不得再从 core JSON 的历史兼容字段读取。
    topology = dict(source.previous_topology or {})
    payload = {
        # 算法合同升级后，同一来源必须能够发布新的正式评估；否则历史错误版本会
        # 被输入幂等误判为当前结果。合同版本不随 WorkflowRun 改变，重试仍然幂等。
        "scoringContractVersion": _POST_INTERVIEW_SCORING_CONTRACT_VERSION,
        "applicationId": source.application_id,
        "stage": source.stage.value,
        "previousAssessmentVersionId": source.source_manifest.previous_assessment_version_id,
        "interviewRecordIds": sorted(
            str(item) for item in source.source_manifest.interview_record_ids
        ),
        "topologySnapshotId": str(
            topology.get("snapshotId") or topology.get("topologySnapshotId") or ""
        ),
        "sourceInputHash": source.source_manifest.source_input_hash,
        # 空值代表首次提交；用户主动重算时使用 Workflow 级稳定标识。不能直接
        # 使用随机调用 ID，否则同一 Workflow 的发布 Step 重试会制造重复版本。
        "recalculationRequestId": recalculation_request_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PostInterviewAssessmentPublisher:
    """在调用方的短事务中发布新 IPR、新 AAV 和必要的状态更新。

    对应七步流程的最后一步：验证步骤 2 至 6 的工件合同，再将面评解析结果固化为新
    ``InterviewParseResultRecord``，并新建指向它的 ``ApplicationAssessmentVersion``。V2 的来源
    必须是冻结 V1，V3 的来源必须是冻结 V2；历史 IPR/AAV/画像版本一律只读。

    这里不调用模型、不读“最新”来源版本；所有输入都来自步骤 1 冻结 Artifact。任何异常都由
    外层 StepRunner 回滚，因此不会出现 IPR 已发布而 AAV 未发布的半成品。
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def _existing_publication(
        self, source: AssessmentSource, publication_key: str
    ) -> ApplicationAssessmentVersion | None:
        """在已加锁的申请内查找同一业务发布请求已经生成的版本。"""
        candidates = self._db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == source.application_id,
                ApplicationAssessmentVersion.stage == source.stage.value,
                ApplicationAssessmentVersion.previous_assessment_version_id
                == source.source_manifest.previous_assessment_version_id,
            )
            .order_by(ApplicationAssessmentVersion.created_at.desc())
        ).all()
        for candidate in candidates:
            source_json = dict(candidate.source_json or {})
            if source_json.get("publication_key") == publication_key:
                return candidate
        return None

    def publish(
        self,
        *,
        user: User,
        application: Application,
        interview: Interview,
        source: AssessmentSource,
        interview_parse_draft: InterviewParseDraft,
        core_result: IncrementalScoringResult,
        rule_result: Mapping[str, Any],
        presentation_result: Mapping[str, Any],
        previous_topology: Mapping[str, Any],
        topology_core_result: Mapping[str, Any],
        workflow_body: Mapping[str, Any],
    ) -> Mapping[str, str]:
        """原子发布本轮唯一正式版本；历史 IPR/AAV 永远只读。

        输入：步骤 1 的冻结来源、步骤 2 的面评草稿、步骤 4 的能力结果、步骤 5 的规则
        结果、步骤 6 的展示结果。输出：恰好一条新 IPR 与一条新 AAV，以及已存在 Target、
        Interview、Application、Task、StageHistory 的允许状态变化。
        禁止：不得发布 ``review_required`` 草稿、不得覆盖前一版本，不得把 Artifact
        作为正式业务对象保存。InterviewTarget 仅由 V1 初筛创建；V2/V3 只能更新冻结时
        已打开 Target 的 ``open/resolved`` 状态，绝不创建或复制新的业务 Target。
        """
        # 同一 Application 的同一面试后阶段必须串行发布，避免版本号竞争。
        locked = lock_business_resources(
            self._db, application_id=application.application_id
        )
        application = locked["application"]
        recalculation_request_id = str(
            workflow_body.get("recalculation_request_id") or ""
        )
        publication_key = _publication_key(
            source, recalculation_request_id=recalculation_request_id
        )
        existing = self._existing_publication(source, publication_key)
        if existing is not None:
            # 同一业务请求的发布步骤重试必须复用，不能再创建第二个 IPR/AAV/Snapshot。
            interview.status = "completed"
            clear_application_recovery_for_prefix(
                application,
                "post_first_"
                if source.stage.value == "after_first_interview"
                else "post_second_",
            )
            return {
                "interviewParseResultId": str(
                    existing.source_interview_parse_result_id or ""
                ),
                "assessmentVersionId": existing.assessment_version_id,
                "applicationId": application.application_id,
                "stage": source.stage.value,
                "reused": "true",
            }
        if interview_parse_draft.review_required:
            # 不能把“待人工确认”的草稿伪装成正式 V2/V3；Worker 会留下可读失败原因，
            # 由受理命令在人工修正记录后重新发起。
            raise RuntimeError("post_interview_parse_review_required")
        core = AssessmentCoreResult.model_validate(core_result.as_dict())
        rule = AssessmentRuleResult.model_validate(rule_result)
        presentation = AssessmentPresentationResult.model_validate(presentation_result)
        resume_id = str(source.resume_profile.get("resume_profile_id") or "")
        job_id = str(source.job_requirement_profile.get("job_profile_id") or "")
        if not resume_id or not job_id:
            raise RuntimeError("post_interview_frozen_profile_reference_missing")

        # 1. 发布前将所有跨步骤 JSON 再次解析为领域契约；任何非法字段必须在写数据库前失败。
        now = _now()
        parse_version = self._next_version(
            InterviewParseResultRecord, source.application_id, source.stage.value
        )
        # 2. 新建本轮 IPR：只保存统一 assertions、片段状态和追溯引用；不保存能力分。
        parse_row = InterviewParseResultRecord(
            parse_result_id=_id("IPR"),
            application_id=source.application_id,
            interview_id=interview.interview_id,
            stage=source.stage.value,
            version=parse_version,
            parse_schema_version="interview_parse_result_v2",
            source_record_ids=list(interview_parse_draft.source_record_ids),
            segments_json=[item.as_dict() for item in interview_parse_draft.segments],
            assertions_json=[dict(item) for item in interview_parse_draft.assertions],
            parser_version=interview_parse_draft.parser_version,
            source_hash=interview_parse_draft.source_hash,
            no_new_evidence=interview_parse_draft.no_new_evidence,
            resolved_interview_target_ids=list(
                interview_parse_draft.resolved_interview_target_ids
            ),
        )
        self._db.add(parse_row)
        self._db.flush()

        # AAV.source_json 严格只保存 AssessmentSourceManifest；画像引用已由 AAV 的
        # 显式外键保存，当前轮 IPR 作为 manifest 的最后一段版本链写入。
        source_json = source.source_manifest.model_copy(
            update={
                "current_interview_parse_result_id": parse_row.parse_result_id,
                "publication_key": publication_key,
            }
        ).model_dump(mode="json")
        # publication_key 属于来源清单的发布身份，不参与评分。
        # 3. 新建本轮 AAV：显式指向冻结画像、上一版本和刚创建 IPR；核心/规则/展示三层分别保存。
        assessment = ApplicationAssessmentVersion(
            assessment_version_id=_id("ASV"),
            application_id=source.application_id,
            stage=source.stage.value,
            version=self._next_version(
                ApplicationAssessmentVersion, source.application_id, source.stage.value
            ),
            previous_assessment_version_id=source.source_manifest.previous_assessment_version_id,
            resume_profile_id=resume_id,
            job_profile_id=job_id,
            source_interview_parse_result_id=parse_row.parse_result_id,
            source_json=source_json,
            core_result_json=core.model_dump(mode="json"),
            rule_result_json=rule.model_dump(mode="json"),
            presentation_json=presentation.model_dump(mode="json"),
            published_at=now,
        )
        self._db.add(assessment)
        # 新 AAV 是后继拓扑快照的父记录，先 flush 再写入拓扑子表。
        self._db.flush()
        # 4. 新 AAV
        # 4. 新 AAV 与后继 Snapshot 同属发布事务。V3 必须只读取这份 V2 Snapshot，
        # 因此不得在下一轮重新从 Profile 构图，也不能允许 AAV 成功但 Snapshot 缺失。
        V1TopologyPersistenceService(self._db, id_factory=_id).persist_successor(
            assessment=assessment,
            application_id=application.application_id,
            previous_topology=previous_topology,
            topology_core_result=topology_core_result,
        )
        # 5. V2/V3 只更新 V1 已建立且当前仍打开的 Target；未知 ID 一律拒绝，
        # 防止每轮评分复制一套目标。发布器绝不根据展示文案自行推断业务状态。
        self._apply_target_updates(source, rule.target_updates)

        # 5. 发布完成只更新评估侧对象和本轮面试任务。Application 主状态已由用户提交面试
        # 决定时原子推进；V2/V3 的成功、失败都不能再改写或回滚它。
        task_type = (
            "conduct_first_interview"
            if source.stage.value == "after_first_interview"
            else "conduct_second_interview"
        )
        task_service = TaskWriteService(self._db)
        task_service.complete(
            application_id=application.application_id, task_type=task_type
        )
        if (
            source.stage.value == "after_first_interview"
            and application.status == "hr_second_review"
        ):
            # 只有 V2 正式发布后，HR 才获得可操作的二面审核待办；处理中/失败时不创建。
            task_service.ensure_pending(
                application_id=application.application_id,
                task_type="review_for_second_interview",
                title="审核一面后评估并决定是否进入二面",
                assignee_user_id=self._resolve_hr_assignee_id(application),
                assignee_role="hr",
            )
        elif (
            source.stage.value == "after_second_interview"
            and application.status == "final_review"
        ):
            # V3 正式发布是“最终决策”待办的唯一创建时点。二面评分处理中、失败或
            # 尚未发布时都不能提前暴露该业务操作，避免用户基于未完成评估作决定。
            task_service.ensure_pending(
                application_id=application.application_id,
                task_type="make_final_decision",
                title="审核二面后评估并作出最终决定",
                assignee_user_id=self._resolve_hr_assignee_id(application),
                assignee_role="hr",
            )
        interview.status = "completed"
        clear_application_recovery_for_prefix(
            application,
            "post_first_"
            if source.stage.value == "after_first_interview"
            else "post_second_",
        )
        self._db.flush()
        return {
            "interviewParseResultId": parse_row.parse_result_id,
            "assessmentVersionId": assessment.assessment_version_id,
            "applicationId": application.application_id,
            "stage": source.stage.value,
        }

    def _resolve_hr_assignee_id(self, application: Application) -> str:
        """返回已冻结的 HR 负责人，并为迁移前旧申请补齐唯一默认 HR。"""
        if application.assigned_hr:
            return application.assigned_hr
        row = self._db.scalars(
            select(User)
            .where(
                User.role == "hr", User.is_active.is_(True), User.deleted_at.is_(None)
            )
            .order_by(User.user_id.asc())
        ).first()
        if row is None:
            raise RuntimeError("post_interview_hr_assignee_missing")
        application.assigned_hr = row.user_id
        return row.user_id

    def _next_version(self, model: Any, application_id: str, stage: str) -> int:
        previous = self._db.scalars(
            select(model)
            .where(model.application_id == application_id, model.stage == stage)
            .order_by(model.version.desc(), model.created_at.desc())
        ).first()
        return int(previous.version) + 1 if previous is not None else 1

    def _apply_target_updates(
        self,
        source: AssessmentSource,
        updates: list[InterviewTargetUpdate],
    ) -> None:
        """只更新本申请已有 Open Target 的状态，不允许 V2/V3 创建新记录。

        这是跨阶段稳定性的最终防线：规则层和发布器都必须保证同一业务核验事项
        始终复用 V1 的 interview_target_id；V2/V3 只能把它保持为 open 或关闭为 resolved。
        """
        rows = {
            row.interview_target_id: row
            for row in self._db.scalars(
                select(InterviewTarget).where(
                    InterviewTarget.application_id == source.application_id
                )
            ).all()
        }
        source_open_ids = {
            str(item.get("interview_target_id") or "")
            for item in source.open_targets
            if str(item.get("interview_target_id") or "")
        }
        for update in updates:
            # 规则产物是本轮更新建议，而不是发布前置条件。只允许更新本轮冻结的
            # Open Target；历史数据、人工编辑或规则版本切换带来的其他 ID 均跳过，
            # 不得重新打开已关闭 Target，更不能使整轮 V2/V3 发布回滚。
            if update.interview_target_id not in source_open_ids:
                continue
            row = rows.get(update.interview_target_id)
            if row is None or row.status != "open":
                continue
            # Target 身份、标题、触发原因和 stage_created 属于 V1 固化的业务定义，后续不改写。
            row.status = update.status.value
            row.resolved_stage = (
                update.resolved_stage.value if update.resolved_stage else None
            )
            row.resolution_note = update.resolution_note
            row.updated_at = _now()
