"""V2/V3 评估来源冻结服务。

该服务是后续面评、增量评分能够重放的唯一来源入口：它只读取已发布版本和
不可变面试记录，冻结后后续 Step 不得再次查询“最新”对象。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    AssessmentTopologyDefinition,
    AssessmentTopologyEdge,
    AssessmentTopologyNode,
    AssessmentTopologyNodeReference,
    AssessmentTopologyNodeState,
    AssessmentTopologySnapshot,
    FirstInterviewPlanVersion,
    InterviewGuide,
    InterviewParseResultRecord,
    InterviewQuestion,
    InterviewRecordRecord,
    InterviewTarget,
    JobRequirementProfileRecord,
    JobVersionRecord,
    ResumeProfileRecord,
    User,
)
from backend.app.modules.assessment.domain.assessment_version import (
    AssessmentSourceManifest,
    AssessmentStage,
    InterviewTargetUpdate,
)
from backend.app.modules.interviews.public import (
    interview_parse_result_view, interview_record_view, interview_target_view,
)
from backend.app.modules.assessment.domain.result_normalizer import (
    normalize_assessment_core_result,
    normalize_assessment_rule_result,
)
from backend.app.modules.assessment.services.scoring_source_projection import (
    project_scoring_job_profile,
    project_scoring_resume_profile,
)


@dataclass(frozen=True)
class AssessmentSource:
    """本次评估冻结后的运行时来源；发布时只保存其身份清单和哈希。"""

    application_id: str
    candidate_id: str
    stage: AssessmentStage
    previous_assessment: Mapping[str, Any]
    previous_core_result: Mapping[str, Any]
    previous_rule_result: Mapping[str, Any]
    # 上一已发布版本的规范化拓扑与节点状态。V2/V3 只允许以它为评分范围，
    # 不得从简历或岗位画像重新发现节点。
    previous_topology: Mapping[str, Any]
    resume_profile: Mapping[str, Any]
    job_requirement_profile: Mapping[str, Any]
    interview_records: Sequence[Mapping[str, Any]]
    open_targets: Sequence[Mapping[str, Any]]
    prior_interview_parse_results: Sequence[Mapping[str, Any]]
    source_manifest: AssessmentSourceManifest
    first_interview_plan: Mapping[str, Any] | None = None


class AssessmentSourceService:
    """锁定 V2/V3 已发布来源，之后的步骤禁止再查询“最新”版本。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def lock(
        self,
        *,
        user: User,
        application: Application,
        body: Mapping[str, Any],
        stage: AssessmentStage,
        previous_stage: AssessmentStage,
        interview_kind: str,
    ) -> AssessmentSource:
        """读取并验证本轮唯一的来源版本，返回不可变运行时快照。"""
        del user  # 权限已由受理命令校验；冻结阶段只读取正式对象。
        if application.deleted_at is not None:
            raise RuntimeError("assessment_source_application_deleted")
        if not application.candidate_id:
            raise RuntimeError("assessment_source_candidate_missing")

        previous = self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == application.application_id,
                ApplicationAssessmentVersion.stage == previous_stage.value,
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
        ).first()
        if previous is None:
            raise RuntimeError(f"assessment_previous_version_missing:{previous_stage.value}")

        resume = self.db.get(ResumeProfileRecord, previous.resume_profile_id)
        job = self.db.get(JobRequirementProfileRecord, previous.job_profile_id)
        if resume is None or resume.candidate_id != application.candidate_id:
            raise RuntimeError("assessment_frozen_resume_profile_missing")
        if job is None or job.job_id != application.job_id:
            raise RuntimeError("assessment_frozen_job_profile_missing")

        # 1. 只冻结本轮不可变原始 InterviewRecord；请求显式指定 ID 时还要校验集合未变化。
        stage_name = "interview_1" if interview_kind == "first" else "interview_2"
        requested_ids = _string_list(body.get("_sourceInterviewRecordIds"))
        records_query = (
            select(InterviewRecordRecord)
            .where(
                InterviewRecordRecord.application_id == application.application_id,
                InterviewRecordRecord.stage == stage_name,
            )
            .order_by(InterviewRecordRecord.created_at.asc(), InterviewRecordRecord.record_id.asc())
        )
        if requested_ids:
            records_query = records_query.where(InterviewRecordRecord.record_id.in_(requested_ids))
        record_rows = list(self.db.scalars(records_query).all())
        # “未填写面评”是允许的业务输入：本轮仍需发布一份无新增证据的 V2/V3，
        # 以冻结“本轮没有新事实”这一结论。后续纯算法会跳过语义模型调用并复用上一版分数。
        if requested_ids and {row.record_id for row in record_rows} != set(requested_ids):
            raise RuntimeError("assessment_interview_record_set_changed")
        interview_records = tuple(_record_view(row) for row in record_rows)

        target_rows = self.db.scalars(
            select(InterviewTarget)
            .where(
                InterviewTarget.application_id == application.application_id,
                InterviewTarget.status == "open",
            )
            .order_by(InterviewTarget.created_at.asc(), InterviewTarget.id.asc())
        ).all()
        # InterviewTarget 是数据库实体，application_id 等归属字段不得越过冻结边界。
        # V2/V3 规则只接收严格的 InterviewTargetUpdate 合同，避免 ORM payload 污染规则结果。
        open_targets = tuple(_open_target_view(row) for row in target_rows)

        # 2. V2 需要精确冻结其已确认题单；V3 的自由记录不得依赖或读取题单。
        first_plan: Mapping[str, Any] | None = None
        if interview_kind == "first":
            first_plan = self._load_confirmed_first_plan(
                application.application_id, previous.assessment_version_id
            )

        # 3. 通过前一 AAV 的显式指针向后追溯 IPR；V2 得到空链，V3 得到一面 IPR。
        prior_iprs = self._load_prior_interview_parse_results(previous)
        prior_ipr_ids = [str(item["parse_result_id"]) for item in prior_iprs]

        # 4. V2/V3 与 V1 复用同一评分来源合同，避免主键/模型版本只存在于 ORM 列时丢失。
        job_version = self.db.get(JobVersionRecord, job.jd_version_id)
        if job_version is None:
            raise RuntimeError("assessment_frozen_job_version_missing")
        resume_profile = project_scoring_resume_profile(resume)
        job_profile = project_scoring_job_profile(job, job_version)
        previous_core = normalize_assessment_core_result(dict(previous.core_result_json or {}))

        # 5. 节点和边仍只来自前一 AAV 的规范化拓扑。面评活动需要的 description/region
        # 只能按这些节点已有的稳定引用，从同一批冻结画像和核心结果中查回。
        from backend.app.modules.assessment.services.topology_semantics import (
            enrich_frozen_topology,
        )

        previous_topology = enrich_frozen_topology(
            self._load_topology_snapshot(previous),
            resume_profile=resume_profile,
            job_profile=job_profile,
            previous_core_result=previous_core,
        )
        manifest_payload = {
            "application_id": application.application_id,
            "candidate_id": application.candidate_id,
            "stage": stage.value,
            "previous_assessment_version_id": previous.assessment_version_id,
            "resume_profile_id": resume.resume_profile_id,
            "job_profile_id": job.job_profile_id,
            "prior_interview_parse_result_ids": prior_ipr_ids,
            "interview_record_ids": [item["record_id"] for item in interview_records],
            "open_target_ids": [str(item.get("interview_target_id") or "") for item in open_targets],
            "first_interview_plan_version_id": first_plan.get("plan_version_id") if first_plan else None,
            "first_interview_guide": first_plan.get("confirmed_guide") if first_plan else None,
            "first_interview_questions": first_plan.get("confirmed_questions") if first_plan else [],
            "topology_snapshot_id": previous_topology["snapshotId"],
            "topology_state_hash": previous_topology.get("stateHash"),
        }
        manifest = AssessmentSourceManifest(
            previous_assessment_version_id=previous.assessment_version_id,
            prior_interview_parse_result_ids=prior_ipr_ids,
            interview_record_ids=manifest_payload["interview_record_ids"],
            first_interview_plan_version_id=manifest_payload["first_interview_plan_version_id"],
            first_interview_guide_hash=(
                _stable_hash(
                    {
                        "guide": manifest_payload["first_interview_guide"],
                        "questions": manifest_payload["first_interview_questions"],
                    }
                )
                if first_plan
                else None
            ),
            algorithm_versions={"interview_evaluation": "v1", "incremental_scoring": "v2"},
            rule_version="incremental_assessment_rule_v2",
            prompt_version="incremental_assessment_presentation_v2",
            source_input_hash=_stable_hash(manifest_payload),
        )
        previous_rule = normalize_assessment_rule_result(dict(previous.rule_result_json or {}))
        return AssessmentSource(
            application_id=application.application_id,
            candidate_id=application.candidate_id,
            stage=stage,
            previous_assessment={
                "assessment_version_id": previous.assessment_version_id,
                "stage": previous.stage,
                "version": previous.version,
                "source": dict(previous.source_json or {}),
                "core": previous_core,
                "rule": previous_rule,
                "presentation": dict(previous.presentation_json or {}),
            },
            previous_core_result=previous_core,
            previous_rule_result=previous_rule,
            previous_topology=previous_topology,
            resume_profile=resume_profile,
            job_requirement_profile=job_profile,
            interview_records=interview_records,
            open_targets=open_targets,
            prior_interview_parse_results=prior_iprs,
            source_manifest=manifest,
            first_interview_plan=first_plan,
        )

    def _load_topology_snapshot(
        self, assessment: ApplicationAssessmentVersion,
    ) -> Mapping[str, Any]:
        """读取正式评估的冻结评分拓扑，转换为算法包可重放的普通字典。

        结构来自 Definition/Node/Edge/Reference，分数只来自本 Assessment 的
        NodeState；两者分表保存，避免 V2/V3 把新分数写回 V1 的定义层。
        """
        snapshot = self.db.scalar(select(AssessmentTopologySnapshot).where(
            AssessmentTopologySnapshot.assessment_version_id == assessment.assessment_version_id
        ))
        if snapshot is None or not snapshot.topology_definition_id:
            raise RuntimeError("assessment_previous_topology_snapshot_missing")
        definition = self.db.get(AssessmentTopologyDefinition, snapshot.topology_definition_id)
        if definition is None:
            raise RuntimeError("assessment_previous_topology_definition_missing")
        rows = list(self.db.execute(
            select(AssessmentTopologyNode, AssessmentTopologyNodeState)
            .join(AssessmentTopologyNodeState, AssessmentTopologyNodeState.topology_node_id == AssessmentTopologyNode.topology_node_id)
            .where(AssessmentTopologyNode.topology_definition_id == definition.topology_definition_id, AssessmentTopologyNodeState.topology_snapshot_id == snapshot.topology_snapshot_id)
            .order_by(AssessmentTopologyNode.ordinal.asc(), AssessmentTopologyNode.topology_node_id.asc())
        ).all())
        if not rows:
            raise RuntimeError("assessment_previous_topology_state_missing")
        node_ids = [node.topology_node_id for node, _state in rows]
        ref_rows = self.db.scalars(select(AssessmentTopologyNodeReference).where(
            AssessmentTopologyNodeReference.topology_node_id.in_(node_ids)
        ).order_by(AssessmentTopologyNodeReference.topology_node_id.asc())).all()
        refs_by_node: dict[str, list[dict[str, Any]]] = {}
        for reference in ref_rows:
            refs_by_node.setdefault(reference.topology_node_id, []).append({"role": reference.reference_role, "kind": reference.reference_kind, "id": reference.reference_id})
        edges = self.db.scalars(select(AssessmentTopologyEdge).where(
            AssessmentTopologyEdge.topology_definition_id == definition.topology_definition_id
        ).order_by(AssessmentTopologyEdge.topology_edge_id.asc())).all()
        return {
            "snapshotId": snapshot.topology_snapshot_id,
            "definitionId": definition.topology_definition_id,
            "stateSchemaVersion": snapshot.state_schema_version,
            "structureSchemaVersion": definition.structure_schema_version,
            "structureHash": definition.structure_hash,
            "stateHash": snapshot.state_hash,
            "nodes": [{"nodeId": node.topology_node_id, "anchorId": node.anchor_key, "anchorType": node.anchor_type, "ordinal": node.ordinal, "score": state.score_value, "level": state.level_value, "state": state.state, "references": refs_by_node.get(node.topology_node_id, [])} for node, state in rows],
            "edges": [{"parentNodeId": edge.parent_node_id, "childNodeId": edge.child_node_id, "relation": edge.relation_type} for edge in edges],
        }
    def _load_confirmed_first_plan(
        self, application_id: str, expected_assessment_version_id: str
    ) -> Mapping[str, Any] | None:
        """读取实际执行的一面题单，缺失时保留面评原文继续。

        题单是理解逐题回答的上下文，不是 V2 的评分拓扑来源。V1 来源重建后，
        已执行题单仍应忠实反映当时的问题；不能因其绑定旧 V1 就阻断最新 V1
        上的增量评分。InterviewRecord 自身保存了回答原文，题单/Guide 缺失时
        仍可把自由记录和可识别回答作为本轮证据宽容处理。
        """
        plan = self.db.scalars(
            select(FirstInterviewPlanVersion)
            .where(
                FirstInterviewPlanVersion.application_id == application_id,
                FirstInterviewPlanVersion.status == "confirmed",
            )
            .order_by(FirstInterviewPlanVersion.version.desc(), FirstInterviewPlanVersion.created_at.desc())
        ).first()
        if plan is None:
            return None
        # ``expected_assessment_version_id`` 表达当前评分基线；它与题单来源不同是
        # 合法的 V1 重建场景，不能把题单误当成评分基线。
        del expected_assessment_version_id
        guide = self.db.scalars(
            select(InterviewGuide)
            .where(
                InterviewGuide.application_id == application_id,
                InterviewGuide.plan_version_id == plan.plan_version_id,
            )
            .order_by(InterviewGuide.created_at.desc(), InterviewGuide.id.desc())
        ).first()
        question_rows = []
        if guide is not None and guide.guide_id:
            question_query = select(InterviewQuestion).where(
                InterviewQuestion.application_id == application_id,
                InterviewQuestion.plan_version_id == plan.plan_version_id,
                InterviewQuestion.guide_id == guide.guide_id
            )
            question_rows = list(self.db.scalars(
                question_query.order_by(
                    InterviewQuestion.created_at.asc(), InterviewQuestion.id.asc()
                )
            ).all())
        confirmed_guide = dict(guide.content_json or {}) if guide is not None else {}
        confirmed_questions = [dict(row.question_json or {}) for row in question_rows]
        # V1 没有产生 Open InterviewTarget 时，面试官确认的空题单也是正式业务结果。
        # V2 仍可使用不可变的自由面评；这里不能再反向要求题单至少包含一道题。
        return {
            "plan_version_id": plan.plan_version_id,
            "guide_id": guide.guide_id if guide is not None else None,
            "source_assessment_version_id": plan.source_assessment_version_id,
            "confirmed_guide": confirmed_guide,
            "confirmed_questions": confirmed_questions,
            "core_result": dict(plan.core_result_json or {}),
            "rule_result": dict(plan.rule_result_json or {}),
            "presentation": dict(plan.presentation_json or {}),
        }

    def _load_prior_interview_parse_results(
        self, previous: ApplicationAssessmentVersion
    ) -> tuple[Mapping[str, Any], ...]:
        """沿 AAV.previous_assessment_version_id 返回已发布 IPR 的时间顺序链。"""
        values: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        cursor: ApplicationAssessmentVersion | None = previous
        while cursor is not None and cursor.assessment_version_id not in seen:
            seen.add(cursor.assessment_version_id)
            parse_id = str(cursor.source_interview_parse_result_id or "")
            if parse_id:
                row = self.db.get(InterviewParseResultRecord, parse_id)
                if row is None:
                    raise RuntimeError(f"assessment_prior_interview_parse_missing:{parse_id}")
                values.append(interview_parse_result_view(row))
            previous_id = str(cursor.previous_assessment_version_id or "")
            cursor = self.db.get(ApplicationAssessmentVersion, previous_id) if previous_id else None
        values.reverse()
        return tuple(values)



def _open_target_view(row: InterviewTarget) -> dict[str, Any]:
    """只从 InterviewTarget 的具名业务字段构造 V2/V3 规则输入。"""
    # attributes 是数据库侧扩展信息，不属于 V2/V3 规则合同，冻结时必须显式隔离。
    view = interview_target_view(row)
    view.pop("attributes", None)
    return InterviewTargetUpdate.model_validate(view).model_dump(mode="json")


def _record_view(row: InterviewRecordRecord) -> dict[str, Any]:
    return interview_record_view(row)


def _string_list(value: Any) -> list[str]:
    return list(dict.fromkeys(str(item) for item in value if item)) if isinstance(value, list) else []


def _stable_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
