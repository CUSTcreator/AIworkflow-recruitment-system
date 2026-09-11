from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.workflow_runtime import WorkflowQueue
from backend.app.models.entities import Application, Candidate, ResumeProfileRecord, ResumeSubmission, SourceDocument, User, WorkflowRun
from backend.app.modules.auth.public import AuthorizationService
from backend.app.modules.candidates.resume_submission_status import ResumeIntakeMode, ResumeSubmissionStatus
from backend.app.modules.candidates.intake_process_service import CandidateIntakeProcessService
from backend.app.shared.audit import record_audit_event
from backend.app.modules.applications.public import ResumeRebuildRequest, apply_candidate_resume_rebuild
from backend.app.shared.errors import BusinessError
from backend.app.storage.object_store import ObjectStore


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CandidateResumeRebuildService:
    """候选人级简历重建编排。

    Candidate 是命令入口和当前版本指针的持有者；一次处理的状态、文本和
    结果只写入 ResumeSubmission。Application 保持招聘主状态，并只在新版
    成功后切换自己采用的 ResumeSubmission。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.queue = WorkflowQueue(db)

    def request_reparse(self, *, submission: ResumeSubmission, user: User, force_fresh_parse: bool = True) -> tuple[ResumeSubmission, WorkflowRun]:
        candidate = self._candidate(submission)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "force_fresh_parse"
        )
        if candidate.current_resume_submission_id != submission.resume_submission_id:
            raise BusinessError(
                "candidate_resume_rebuild_not_current", "只能重新解析候选人当前采用的简历", status_code=409
            )
        if submission.status not in {
            ResumeSubmissionStatus.COMPLETED.value,
            ResumeSubmissionStatus.REVIEW_REQUIRED.value,
            ResumeSubmissionStatus.FAILED.value,
        }:
            raise BusinessError(
                "candidate_resume_rebuild_not_ready", "当前简历仍在处理中，暂不能重新解析", status_code=409
            )
        active = self._active_run(submission.resume_submission_id)
        if active is not None:
            return submission, active
        document = self._document(submission)
        next_submission = ResumeSubmission(
            resume_submission_id=_id("RSUB"),
            source_document_id=document.source_document_id,
            job_id=None,
            idempotency_key=f"candidate-reparse:{uuid.uuid4().hex}",
            external_candidate_id=None,
            external_application_id=None,
            candidate_name_override=submission.candidate_name_override,
            status="queued",
            candidate_id=candidate.candidate_id,
            intake_mode="reparse",
            parse_strategy="force_fresh_parse" if force_fresh_parse else "reuse_verified_parse",
            supersedes_submission_id=submission.resume_submission_id,
            rebuild_from_submission_id=submission.resume_submission_id,
            application_id=None,
            business_submitted_at=_now(),
            error_message=None,
            uploaded_by=user.user_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(next_submission)
        self.db.flush()
        candidate.current_resume_submission_id = next_submission.resume_submission_id
        now = _now()
        CandidateIntakeProcessService.activate_candidate(candidate, now=now)
        run = self._enqueue(next_submission, user.user_id, mode="force_fresh_parse" if force_fresh_parse else "reparse")
        record_audit_event(
            self.db, actor=user, action="candidate.resume.reparse_requested",
            target_type="resume_submission", target_id=next_submission.resume_submission_id,
            summary="请求重新解析候选人当前简历",
            details={
                "candidateId": candidate.candidate_id,
                "supersedesSubmissionId": submission.resume_submission_id,
                "parseStrategy": next_submission.parse_strategy,
            },
            workflow_run_id=run.workflow_run_id,
        )
        return next_submission, run

    def request_publish_retry(
        self, *, submission: ResumeSubmission, user: User
    ) -> tuple[ResumeSubmission, WorkflowRun]:
        """Create a new version that reuses verified parse and structure artifacts.

        A failed database publication must not trigger another MinerU or LLM call.
        The child Submission keeps audit lineage while the import workflow replays only
        its deterministic artifact-read and publication steps.
        """
        candidate = self._candidate(submission)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "retry_publish_resume"
        )
        if candidate.current_resume_submission_id != submission.resume_submission_id:
            raise BusinessError("candidate_resume_rebuild_not_current", "只能重新发布候选人当前采用的简历", status_code=409)
        if str(submission.status) != ResumeSubmissionStatus.FAILED.value:
            raise BusinessError("candidate_resume_publish_retry_not_failed", "只有发布失败的简历任务可以重新发布", status_code=409)
        if not submission.structure_result_ref or not submission.structure_result_sha256:
            raise BusinessError("candidate_resume_publish_retry_artifact_missing", "没有可复用的结构化结果，请校正或重新解析简历", status_code=409)
        if not str(submission.parsed_text or "").strip():
            raise BusinessError("candidate_resume_publish_retry_parse_missing", "没有可复用的解析文本，请重新解析简历", status_code=409)
        document = self._document(submission)
        next_submission = ResumeSubmission(
            resume_submission_id=_id("RSUB"),
            source_document_id=document.source_document_id,
            job_id=None,
            idempotency_key=f"candidate-publish-retry:{uuid.uuid4().hex}",
            external_candidate_id=None,
            external_application_id=None,
            candidate_name_override=submission.candidate_name_override,
            status="queued",
            candidate_id=candidate.candidate_id,
            intake_mode=ResumeIntakeMode.REPARSE.value,
            parse_strategy="reuse_verified_parse",
            supersedes_submission_id=submission.resume_submission_id,
            rebuild_from_submission_id=submission.resume_submission_id,
            output_resume_profile_id=None,
            parsed_text=submission.parsed_text,
            parse_result_json=dict(submission.parse_result_json or {}),
            structure_result_ref=submission.structure_result_ref,
            structure_result_sha256=submission.structure_result_sha256,
            structure_schema_version=submission.structure_schema_version,
            structure_metadata_json=dict(submission.structure_metadata_json or {}),
            application_id=None,
            business_submitted_at=_now(),
            error_message=None,
            uploaded_by=user.user_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(next_submission)
        self.db.flush()
        candidate.current_resume_submission_id = next_submission.resume_submission_id
        CandidateIntakeProcessService.activate_candidate(candidate, now=_now())
        run = self._enqueue(next_submission, user.user_id, mode="publish_retry")
        record_audit_event(
            self.db,
            actor=user,
            action="candidate.resume.publish_retry_requested",
            target_type="resume_submission",
            target_id=next_submission.resume_submission_id,
            summary="请求重新发布已提取的简历结果",
            details={
                "candidateId": candidate.candidate_id,
                "supersedesSubmissionId": submission.resume_submission_id,
                "reusedStructureRef": submission.structure_result_ref,
            },
            workflow_run_id=run.workflow_run_id,
        )
        return next_submission, run

    def correction_draft(self, *, submission: ResumeSubmission, user: User) -> dict:
        """Return structured resume facts plus immutable source blocks.

        The page is a user-facing review of the structure stage, not a debug view of
        WorkUnit extraction.  Therefore ``context_items`` and ``source_bullets`` are
        exposed as readable text, while scoring-only ``work_units`` stay server-side.
        Source IDs remain in the response only as opaque values used to submit a
        correction; the frontend must never render them as user-facing labels.
        """
        candidate = self._candidate(submission)
        AuthorizationService(self.db).require_resume_submission_material_view(
            user, submission
        )
        if candidate.current_resume_submission_id != submission.resume_submission_id:
            raise BusinessError("candidate_resume_rebuild_not_current", "只能校正候选人当前采用的简历", status_code=409)
        blocks = self._parsed_blocks(submission)
        source_blocks = [
            {
                "block_id": str(item.get("block_id") or ""),
                "text": str(item.get("text") or ""),
                "source_line_start": item.get("source_line_start") or item.get("line_start"),
                "source_line_end": item.get("source_line_end") or item.get("line_end"),
            }
            for item in list(blocks.get("blocks") or [])
            if str(item.get("block_id") or "") and str(item.get("text") or "").strip()
        ]
        block_text = {item["block_id"]: item["text"] for item in source_blocks}

        def refs_from_ids(block_ids) -> list[dict[str, str]]:
            refs: list[dict[str, str]] = []
            seen: set[str] = set()
            for block_id in list(block_ids or []):
                normalized_id = str(block_id or "")
                if not normalized_id or normalized_id in seen or normalized_id not in block_text:
                    continue
                seen.add(normalized_id)
                refs.append({"block_id": normalized_id, "quote": block_text[normalized_id]})
            return refs

        def refs_from_items(items) -> list[dict[str, str]]:
            ids = [
                block_id
                for item in list(items or [])
                if isinstance(item, dict)
                for block_id in list(item.get("source_block_ids") or [])
            ]
            return refs_from_ids(ids)

        def normalize_refs(items) -> list[dict[str, str]]:
            """Bind published quote-only provenance back to immutable parse blocks."""
            ids: list[str] = []
            quotes: list[str] = []
            for item in list(items or []):
                if not isinstance(item, dict):
                    continue
                block_id = str(item.get("block_id") or "")
                quote = str(item.get("quote") or "").strip()
                if block_id:
                    ids.append(block_id)
                elif quote:
                    quotes.append(quote)
            ids.extend(
                block_id
                for quote in quotes
                for block_id, text in block_text.items()
                if quote in text
            )
            return refs_from_ids(ids)

        # The immutable structure artifact is the source of truth for this page because
        # it contains the pre-WorkUnit fields (experience units, context items and source
        # bullets).  A published profile is a deliberate fallback for historical rows or
        # an unavailable object-store read; viewing a correction draft must remain usable.
        structure_ir = self._read_structure_ir(submission)
        profile = self.db.get(ResumeProfileRecord, submission.output_resume_profile_id) if submission.output_resume_profile_id else None
        profile_json = dict(profile.profile_data or {}) if profile is not None else {}
        structured_json = structure_ir or profile_json
        structured_context_by_project: dict[str, list[dict]] = {}
        for value in list(structured_json.get("project_context_items") or []):
            if not isinstance(value, dict):
                continue
            project_id = str(value.get("experience_unit_id") or "")
            if project_id:
                structured_context_by_project.setdefault(project_id, []).append(value)
        structured_bullets_by_project: dict[str, list[dict]] = {}
        for value in list(structured_json.get("source_bullets") or []):
            if not isinstance(value, dict):
                continue
            project_id = str(value.get("experience_unit_id") or "")
            if project_id:
                structured_bullets_by_project.setdefault(project_id, []).append(value)
        editable_experiences: list[dict] = []
        for item in list(structured_json.get("experience_units") or []):
            if not isinstance(item, dict):
                continue
            project_id = str(item.get("experience_unit_id") or "")
            title = str(item.get("title") or "").strip()
            all_context_items = [
                value for value in structured_context_by_project.get(project_id, [])
                if isinstance(value, dict)
            ]
            if not all_context_items:
                all_context_items = [
                    value for value in list(item.get("context_items") or [])
                    if isinstance(value, dict)
                ]
            context_items = [
                value for value in all_context_items
                if str(value.get("context_type") or "") != "project_title"
            ]
            # Historical published profiles do not retain top-level SourceBullet rows;
            # derive a conservative fallback from their WorkUnit source references.
            work_units = [value for value in list(item.get("work_units") or []) if isinstance(value, dict)]
            source_bullets = structured_bullets_by_project.get(project_id, [])
            if not source_bullets:
                source_bullets = [
                    {
                        "source_bullet_id": str(value.get("source_bullet_id") or ""),
                        "raw_text": str(value.get("raw_text") or "").strip(),
                        "source_block_ids": list(value.get("source_block_ids") or []),
                        "source_refs": list(value.get("source_refs") or []),
                    }
                    for value in work_units
                    if str(value.get("raw_text") or "").strip()
                ]
            title_refs = normalize_refs(item.get("title_source_refs"))
            if not title_refs:
                title_contexts = [
                    value for value in all_context_items
                    if str(value.get("context_type") or "") == "project_title"
                ]
                title_refs = refs_from_items(title_contexts)
            if not title_refs and title:
                title_refs = refs_from_ids([
                    block_id for block_id, text in block_text.items() if title in text
                ])
            editable_experiences.append({
                "experience_unit_id": str(item.get("experience_unit_id") or ""),
                "title": title,
                "context_items": [
                    {
                        "context_id": str(value.get("context_id") or ""),
                        "context_type": str(value.get("context_type") or "other_context"),
                        "text": (
                            str(value.get("text") or "").strip()
                            or " ".join(
                                block_text.get(str(block_id), "")
                                for block_id in list(value.get("source_block_ids") or [])
                                if block_text.get(str(block_id), "")
                            ).strip()
                        ),
                        "source_refs": self._refs_for_structured_item(
                            value, normalize_refs=normalize_refs, refs_from_ids=refs_from_ids
                        ),
                    }
                    for value in context_items
                    if str(value.get("text") or "").strip()
                ],
                "source_bullets": [
                    {
                        "source_bullet_id": str(value.get("source_bullet_id") or ""),
                        "text": str(value.get("raw_text") or value.get("text") or "").strip(),
                        "source_refs": self._refs_for_structured_item(
                            value, normalize_refs=normalize_refs, refs_from_ids=refs_from_ids
                        ),
                    }
                    for value in source_bullets
                    if str(value.get("raw_text") or value.get("text") or "").strip()
                ],
                "title_source_refs": title_refs,
                "context_source_refs": (
                    normalize_refs(item.get("context_source_refs"))
                    or refs_from_items(context_items)
                ),
                "work_source_refs": (
                    normalize_refs(item.get("work_source_refs"))
                    or refs_from_items(source_bullets)
                    or refs_from_items(work_units)
                ),
            })
        # 技能提取在结构化工件中按“原文声明 -> 技能项”分组保存；校正页只展示并
        # 编辑扁平的技能项。优先读取本次结构化结果，历史数据才回退到已发布画像。
        structured_skills = [
            {
                **claim,
                "source_refs": list(claim.get("source_refs") or [])
                or ([statement.get("source_ref")] if statement.get("source_ref") else []),
            }
            for statement in list(structured_json.get("skill_statements") or [])
            if isinstance(statement, dict)
            for claim in list(statement.get("skill_claims") or [])
            if isinstance(claim, dict)
        ]
        skill_items = structured_skills or [
            item for item in list(profile_json.get("skill_claims") or [])
            if isinstance(item, dict)
        ]
        editable_skills: list[dict] = []
        for item in skill_items:
            if not isinstance(item, dict):
                continue
            editable_skills.append({
                **item,
                "source_refs": normalize_refs(item.get("source_refs")),
            })
        return {
            "submission_id": submission.resume_submission_id,
            "source_blocks": source_blocks,
            "candidate_facts": dict(structured_json.get("candidate_facts") or profile_json.get("candidate_facts") or {}),
            "experience_units": editable_experiences,
            "skill_claims": editable_skills,
        }

    @staticmethod
    def _refs_for_structured_item(
        item: dict,
        *,
        normalize_refs,
        refs_from_ids,
    ) -> list[dict[str, str]]:
        """Normalize either block IDs or quote provenance to the internal source form."""
        refs = normalize_refs(item.get("source_refs"))
        return refs or refs_from_ids(item.get("source_block_ids"))

    def _read_structure_ir(self, submission: ResumeSubmission) -> dict:
        """读取结构化阶段的用户可理解事实，不向页面暴露评分中间产物。"""
        structure_ref = str(getattr(submission, "structure_result_ref", None) or "").strip()
        if not structure_ref:
            return {}
        try:
            payload = ObjectStore().read_json(
                structure_ref,
                f"documents/{submission.source_document_id}/submissions/{submission.resume_submission_id}/resume_structure_v2.json",
            )
        except Exception:
            # 这是读取展示工件的降级，不改变业务状态；调用方会回退到 ResumeProfile。
            return {}
        value = payload.get("resume_ir") if isinstance(payload, dict) else None
        return dict(value) if isinstance(value, dict) else {}

    def request_manual_correction(
        self,
        *,
        submission: ResumeSubmission,
        user: User,
        correction: dict,
    ) -> tuple[ResumeSubmission, WorkflowRun]:
        """Create a child Submission that reuses immutable parsing and applies user edits."""
        candidate = self._candidate(submission)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "correct_parsed_resume"
        )
        if candidate.current_resume_submission_id != submission.resume_submission_id:
            raise BusinessError("candidate_resume_rebuild_not_current", "只能校正候选人当前采用的简历", status_code=409)
        if submission.status not in {
            ResumeSubmissionStatus.COMPLETED.value,
            ResumeSubmissionStatus.REVIEW_REQUIRED.value,
            ResumeSubmissionStatus.FAILED.value,
        }:
            raise BusinessError("candidate_resume_correction_not_ready", "当前简历仍在处理中，暂不能校正", status_code=409)
        if not str(submission.parsed_text or "").strip():
            raise BusinessError("candidate_resume_correction_parse_missing", "尚无可校正的解析文本，请先选择重新解析", status_code=409)
        document = self._document(submission)
        normalized = self._normalize_correction(submission, correction)
        next_id = _id("RSUB")
        correction_ref, correction_sha = ObjectStore().put_json(
            f"documents/{document.source_document_id}/submissions/{next_id}/manual_correction_v1.json",
            normalized,
        )
        next_submission = ResumeSubmission(
            resume_submission_id=next_id,
            source_document_id=document.source_document_id,
            job_id=None,
            idempotency_key=f"candidate-manual-correction:{uuid.uuid4().hex}",
            external_candidate_id=None,
            external_application_id=None,
            candidate_name_override=submission.candidate_name_override,
            status="queued",
            candidate_id=candidate.candidate_id,
            intake_mode=ResumeIntakeMode.MANUAL_CORRECTION.value,
            parse_strategy="reuse_verified_parse",
            manual_correction_ref=correction_ref,
            manual_correction_sha256=correction_sha,
            supersedes_submission_id=submission.resume_submission_id,
            rebuild_from_submission_id=submission.resume_submission_id,
            output_resume_profile_id=None,
            parsed_text=submission.parsed_text,
            parse_result_json=dict(submission.parse_result_json or {}),
            application_id=None,
            business_submitted_at=_now(),
            error_message=None,
            uploaded_by=user.user_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(next_submission)
        self.db.flush()
        candidate.current_resume_submission_id = next_submission.resume_submission_id
        CandidateIntakeProcessService.activate_candidate(candidate, now=_now())
        run = self._enqueue(next_submission, user.user_id, mode="manual_correction")
        record_audit_event(
            self.db,
            actor=user,
            action="candidate.resume.manual_correction_requested",
            target_type="resume_submission",
            target_id=next_submission.resume_submission_id,
            summary="提交已校正的简历解析结果",
            details={
                "candidateId": candidate.candidate_id,
                "supersedesSubmissionId": submission.resume_submission_id,
                "manualCorrectionSha256": correction_sha,
            },
            workflow_run_id=run.workflow_run_id,
        )
        return next_submission, run

    def _parsed_blocks(self, submission: ResumeSubmission) -> dict:
        metadata = dict(submission.parse_result_json or {})
        blocks_ref = str(metadata.get("documentBlocksRef") or metadata.get("document_blocks_ref") or "")
        if not blocks_ref:
            raise BusinessError("candidate_resume_blocks_missing", "解析块不存在，请先选择重新解析", status_code=409)
        try:
            return ObjectStore().read_json(
                blocks_ref,
                f"documents/{submission.source_document_id}/submissions/{submission.resume_submission_id}/manual_correction_blocks.json",
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            # 数据库引用与对象存储不一致时，校正窗口不能以框架 500 结束。用户仍可
            # 通过重新解析/重新上传重建 Block 工件，因此返回可理解的业务冲突。
            raise BusinessError(
                "candidate_resume_blocks_unavailable",
                "解析原文片段暂不可用，请重新解析或重新上传简历",
                status_code=409,
            ) from exc

    def _normalize_correction(self, submission: ResumeSubmission, correction: dict) -> dict:
        blocks = self._parsed_blocks(submission)
        block_text = {
            str(item.get("block_id")): str(item.get("text") or "").strip()
            for item in list(blocks.get("blocks") or [])
            if str(item.get("block_id") or "") and str(item.get("text") or "").strip()
        }

        def refs(block_ids) -> list[dict[str, str]]:
            ids = [str(item) for item in list(block_ids or [])]
            invalid = [item for item in ids if item not in block_text]
            if invalid:
                raise BusinessError("candidate_resume_correction_source_invalid", "校正所选原文片段已失效，请刷新后重试", status_code=422)
            return [{"block_id": item, "quote": block_text[item]} for item in dict.fromkeys(ids)]

        facts = dict(correction.get("candidate_facts") or {})
        education_records = []
        for item in list(facts.get("education_records") or []):
            if not isinstance(item, dict):
                raise BusinessError("candidate_resume_correction_invalid", "教育信息格式无效", status_code=422)
            education_refs = refs(item.get("source_block_ids"))
            if any(str(item.get(field) or "").strip() for field in ("school", "degree_level", "major")) and not education_refs:
                raise BusinessError("candidate_resume_correction_source_required", "教育信息必须选择对应的原文片段", status_code=422)
            degree_level = str(item.get("degree_level") or "other").strip()
            if degree_level not in {"associate", "bachelor", "master", "doctorate", "other"}:
                raise BusinessError("candidate_resume_correction_degree_invalid", "学历层次格式无效", status_code=422)
            status = str(item.get("status") or "unknown").strip()
            if status not in {"completed", "in_progress", "unknown"}:
                raise BusinessError("candidate_resume_correction_education_status_invalid", "教育状态格式无效", status_code=422)
            education_records.append({
                "school": str(item.get("school") or "").strip() or None,
                "major": str(item.get("major") or "").strip() or None,
                "degree_level": degree_level,
                "status": status,
                "start_year": item.get("start_year") if isinstance(item.get("start_year"), int) else None,
                "graduation_year": item.get("graduation_year") if isinstance(item.get("graduation_year"), int) else None,
                "source_refs": education_refs,
            })
        experience = dict(facts.get("relevant_experience_years") or {})
        experience_value = experience.get("value") if isinstance(experience.get("value"), (int, float)) else None
        experience_refs = refs(experience.get("source_block_ids"))
        if experience_value is not None and not experience_refs:
            raise BusinessError("candidate_resume_correction_source_required", "经验年限必须选择对应的原文片段", status_code=422)
        normalized = {
            "schema_version": "resume_manual_correction_v1",
            "candidate_facts": {
                "education_records": education_records,
                "relevant_experience_years": {
                    "value": experience_value,
                    "source_refs": experience_refs,
                },
            },
            "experience_units": [],
            "skill_claims": [],
        }
        for item in list(correction.get("experience_units") or []):
            if not isinstance(item, dict):
                raise BusinessError("candidate_resume_correction_invalid", "经历信息格式无效", status_code=422)
            title = str(item.get("title") or "").strip()
            title_refs = refs(item.get("title_block_ids"))
            work_refs = refs(item.get("work_evidence_block_ids"))
            if not title or not title_refs:
                raise BusinessError("candidate_resume_correction_project_title_required", "每个项目必须填写标题并选择标题原文", status_code=422)
            if not any(title in str(ref.get("quote") or "") for ref in title_refs):
                raise BusinessError("candidate_resume_correction_project_title_unverified", "项目标题必须出现在所选标题原文中", status_code=422)
            if not work_refs:
                raise BusinessError("candidate_resume_correction_work_evidence_required", "每个项目至少选择一段直接工作证据", status_code=422)
            normalized["experience_units"].append({
                "experience_unit_id": str(item.get("experience_unit_id") or "").strip(),
                "title": title,
                "title_source_refs": title_refs,
                "context_source_refs": refs(item.get("context_block_ids")),
                "work_source_refs": work_refs,
            })
        for index, item in enumerate(list(correction.get("skill_claims") or []), start=1):
            if not isinstance(item, dict) or not str(item.get("skill_name") or "").strip():
                raise BusinessError("candidate_resume_correction_invalid", "技能名称不能为空", status_code=422)
            skill_refs = refs(item.get("source_block_ids"))
            if not skill_refs:
                raise BusinessError("candidate_resume_correction_skill_source_required", "每个技能必须选择对应的原文片段", status_code=422)
            normalized["skill_claims"].append({
                "skill_claim_id": f"MANUAL_SC_{index:03d}",
                "skill_name": str(item.get("skill_name")).strip(),
                "details": [str(detail).strip() for detail in list(item.get("details") or []) if str(detail).strip()],
                "source_refs": skill_refs,
            })
        return normalized
    def prepare_replacement(self, *, submission: ResumeSubmission, user: User) -> Candidate:
        candidate = self._candidate(submission)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "upload_replacement_resume"
        )
        return candidate

    def register_replacement(
        self,
        *,
        previous: ResumeSubmission,
        replacement: ResumeSubmission,
        user: User,
    ) -> None:
        """将新上传 PDF 对应任务登记为当前任务，不触碰既有 Application。"""
        candidate = self._candidate(previous)
        if replacement.candidate_id != candidate.candidate_id or replacement.intake_mode != ResumeIntakeMode.REPLACEMENT.value:
            raise BusinessError("candidate_resume_rebuild_mismatch", "新版简历与候选人不匹配", status_code=409)
        if replacement.supersedes_submission_id not in {None, previous.resume_submission_id}:
            raise BusinessError("candidate_resume_rebuild_lineage_invalid", "新版简历的版本来源不正确", status_code=409)
        replacement.supersedes_submission_id = previous.resume_submission_id
        replacement.rebuild_from_submission_id = previous.resume_submission_id
        candidate.current_resume_submission_id = replacement.resume_submission_id
        candidate.updated_at = _now()
        record_audit_event(
            self.db, actor=user, action="candidate.resume.replacement_activated",
            target_type="resume_submission", target_id=replacement.resume_submission_id,
            summary="将新上传简历设为候选人当前版本",
            details={"candidateId": candidate.candidate_id, "supersedesSubmissionId": previous.resume_submission_id},
        )

    def mark_rebuild_review_required(
        self,
        *,
        candidate: Candidate,
        submission: ResumeSubmission,
        reason: str,
    ) -> None:
        """将结构化待确认投影到候选人与其全部附属申请，但不改变招聘主状态。"""
        now = _now()
        CandidateIntakeProcessService.activate_candidate(candidate, now=now)
        candidate.updated_at = now
        for app in self.db.scalars(
            select(Application).where(
                Application.candidate_id == candidate.candidate_id,
                Application.deleted_at.is_(None),
            )
        ):
            app.updated_at = now
    def rebuild_applications(
        self, *, candidate: Candidate, submission: ResumeSubmission,
        profile: ResumeProfileRecord, run_id: str, user: User,
    ) -> list[str]:
        """在新版结构化完成后，通过 Application 契约切换所有有效申请的简历版本。"""
        if submission.candidate_id != candidate.candidate_id:
            raise BusinessError("resume_submission_candidate_mismatch", "简历任务与候选人不匹配", status_code=409)
        if submission.status != ResumeSubmissionStatus.COMPLETED.value:
            raise BusinessError("resume_submission_not_completed", "简历尚未完成结构化，不能切换岗位申请", status_code=409)
        if submission.output_resume_profile_id != profile.resume_profile_id or profile.candidate_id != candidate.candidate_id:
            raise BusinessError("resume_submission_profile_not_finalized", "简历画像与候选人或处理任务不一致", status_code=409)
        document = self._document(submission)
        result = apply_candidate_resume_rebuild(
            self.db,
            command=ResumeRebuildRequest(
                candidate_id=candidate.candidate_id,
                resume_submission_id=submission.resume_submission_id,
                resume_profile_id=profile.resume_profile_id,
                source_document_id=document.source_document_id,
                rebuild_workflow_run_id=run_id,
                idempotency_key=f"{candidate.candidate_id}:{submission.resume_submission_id}:{run_id}",
            ),
            actor_id=user.user_id,
        )
        now = _now()
        # Candidate 自身的“当前简历”指针仍只由 Candidate 模块维护。
        candidate.current_resume_submission_id = submission.resume_submission_id
        candidate.current_resume_profile_id = profile.resume_profile_id
        candidate.updated_at = now
        self.db.flush()
        return result.application_ids
    def _candidate(self, submission: ResumeSubmission) -> Candidate:
        candidate = self.db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
        if candidate is None:
            raise BusinessError("candidate_resume_rebuild_unavailable", "该简历尚未建立候选人档案，不能重新解析", status_code=409)
        return candidate

    def _document(self, submission: ResumeSubmission) -> SourceDocument:
        document = self.db.get(SourceDocument, submission.source_document_id)
        if document is None or document.document_type != "resume":
            raise BusinessError("resume_source_document_not_found", "未找到简历源文件", status_code=404)
        return document

    def _active_run(self, submission_id: str) -> WorkflowRun | None:
        return self.queue.active_run(
            workflow_type="resume_document_import_workflow",
            subject_type="resume_submission",
            subject_id=submission_id,
        )

    def _enqueue(self, submission: ResumeSubmission, user_id: str, *, mode: str) -> WorkflowRun:
        run, _ = self.queue.enqueue(
            workflow_type="resume_document_import_workflow",
            subject_type="resume_submission",
            subject_id=submission.resume_submission_id,
            triggered_by=user_id,
            input_json={"candidate_resume_rebuild": True, "mode": mode},
            reuse_active=False,
        )
        return run
