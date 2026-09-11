# 事务边界静态扫描快照

- 生成时间：2026-08-24 19:34:42 +08:00
- 扫描范围：`backend/app`
- 执行方式：`backend/scripts/audit_transaction_boundaries.ps1`
- 说明：结果是候选点清单；是否违规以人工审计报告为准。

## 1. 事务边界：commit / rollback / flush

```text
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\command_runtime\command_runner.py:94:            self.db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\command_runtime\command_runner.py:104:            self.db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\main.py:37:        db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\database\snapshot_repository.py:28:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\seeds\import_university_rankings.py:83:    db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\seeds\seed_accounts.py:65:    db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\seeds\seed_accounts.py:93:    db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\seeds\seed_accounts.py:128:    db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\seeds\seed_accounts.py:155:    db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:139:        db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:174:                    db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:179:                    db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:182:                db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:211:                db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:213:            db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:266:            db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\workers\workflow_worker.py:325:        db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\artifact_store.py:176:        db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\artifact_store.py:227:            db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\checkpoint_repository.py:51:            db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\checkpoint_repository.py:68:        db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:201:        db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:219:                db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:254:            db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:348:                    db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:352:                    db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:358:                    db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:364:                    db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:375:                    db.rollback()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:399:                db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:433:            db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:465:            db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:500:            db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:530:            db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:574:        db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\step_runner.py:617:            db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\commands\application_decision_commands.py:148:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\commands\application_intake_commands.py:111:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\commands\application_lifecycle_commands.py:97:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\commands\application_lifecycle_commands.py:281:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_service.py:127:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_service.py:142:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_service.py:395:            self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\documents\document_service.py:164:            self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\documents\document_service.py:205:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\documents\document_service.py:217:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\documents\document_service.py:229:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_catalog_service.py:59:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_catalog_service.py:93:        self.db.add(row); self.db.flush(); self._audit(actor, "create", row); self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_catalog_service.py:109:        row.updated_at = _now(); self.db.flush(); self._audit(actor, "update", row); self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_catalog_service.py:116:        row.deleted_at = _now(); row.enabled = False; row.updated_at = _now(); self._audit(actor, "delete", row); self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_catalog_service.py:124:        self._audit(actor, "reset", None); self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\commands\scoring_request_commands.py:221:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\auth\service.py:34:        self.db.commit()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\services\assessment_version_publisher.py:98:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\services\assessment_version_publisher.py:137:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\services\assessment_version_publisher.py:257:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\services\post_interview_assessment_publisher.py:112:        self._db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\services\post_interview_assessment_publisher.py:155:        self._db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\organization_service.py:228:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\operations_service.py:387:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\service.py:86:            self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\service.py:170:            self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\service.py:428:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\account_service.py:199:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\account_service.py:393:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interviews\commands\interview_workflow_request_commands.py:232:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interviews\commands\interview_lifecycle_commands.py:335:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_service.py:219:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_service.py:257:            self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_service.py:278:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_read_models.py:53:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\rebuild_service.py:81:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\rebuild_service.py:189:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:129:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:206:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:220:            self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:246:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:137:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:162:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:349:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:382:                self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:483:                self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:515:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:547:                    self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:620:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\management_read_models.py:172:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\management_read_models.py:279:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interviews\services\first_interview_planning_service.py:301:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interviews\services\first_interview_plan_version_publisher.py:132:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\lifecycle_service.py:52:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\lifecycle_service.py:277:            self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\lifecycle_service.py:293:        self.db.flush()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\services\job_profile_service.py:182:            self.db.flush()
```

## 2. 领域状态直接写入

```text
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_service.py:312:        if app.status == "hard_screening_running":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\domain\application_lifecycle_policy.py:18:    return application.status == "department_review"
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\domain\resume_submission_state_machine.py:82:    submission.status = target_value
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\domain\candidate_lifecycle_state_machine.py:39:    candidate.status = target_value
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\application_process_service.py:63:        application.status = transition.to_status
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\services\assessment_scheduling_service.py:37:        if app.status == "hard_screening_pending":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_service.py:188:        if submission.status == ResumeSubmissionStatus.COMPLETED.value:
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_read_models.py:259:        if submission.status == "failed" or workflow.get("status") == "failed":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_read_models.py:262:            if submission.status == "completed":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_read_models.py:265:        if submission.status == "completed" or has_applications:
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\organization_service.py:200:        if job.status == "open":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\organization_service.py:224:        return self._job_view(job, {job.department_id: department.name if job.status == "open" else (self.repository.department(job.department_id).name if self.repository.department(job.department_id) else "")}, {row.user_id: row.display_name for row in self.db.scalars(select(User))})
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\operations_service.py:468:            if app and app.status == "screening_failed":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\queries\read_models.py:265:        if app.status == "screening_running":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interviews\queries\first_interview_query_service.py:95:        if app.status == "first_interview_planning":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interviews\queries\first_interview_query_service.py:99:        elif app.status == "first_interview_scheduled":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\domain\job_state_machine.py:39:    job.status = target
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\job_document_service.py:578:            if job.status == "closed":
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:215:    if submission.status == ResumeSubmissionStatus.QUEUED.value:
```

## 3. Workflow 入队

```text
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interviews\commands\interview_workflow_request_commands.py:148:        run, _ = self.queue.enqueue(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\candidate_routing_service.py:39:        run, _ = self.queue.enqueue(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\commands\scoring_request_commands.py:152:        run, _ = self.queue.enqueue(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\rebuild_service.py:224:        run, _ = self.queue.enqueue(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\services\job_profile_service.py:86:        run, _ = WorkflowQueue(self.db).enqueue(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\hard_screening\hard_screening_service.py:238:        run, _ = self.queue.enqueue(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:309:        run, _ = self.queue.enqueue(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:343:        run, _ = self.queue.enqueue(
```

## 4. 对象存储 / 外部活动 / after_commit

```text
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:32:        self._activity = activity or ExternalActivity()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:34:    def put_json(self, object_key: str, payload: dict[str, Any]) -> tuple[str, str]:
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:36:        return self.put_bytes(object_key, data, "application/json")
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:38:    def read_json(self, object_ref: str, cache_key: str) -> dict[str, Any]:
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:39:        path = self.materialize(object_ref, cache_key)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:45:    def put_bytes(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:91:    def materialize(self, object_ref: str, cache_key: str) -> Path:
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\storage\object_store.py:112:    def delete_object(self, object_ref: str) -> None:
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\command_runtime\command_contracts.py:62:    def after_commit(self, operation: Callable[[], None]) -> None:
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\model_gateway\openai_compatible.py:353:        self.activity = activity or ExternalActivity()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\services\application_access_service.py:73:            path = self.store.materialize(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\artifact_store.py:86:        object_ref, object_sha256 = ObjectStore().put_bytes(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\infrastructure\workflow_runtime\artifact_store.py:191:            return ObjectStore().read_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\documents\document_router.py:122:                lambda object_ref=object_ref: service.store.delete_object(object_ref)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\documents\document_service.py:140:            object_ref, stored_sha256 = self.store.put_bytes(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\applications\documents\document_service.py:257:        pdf_path = self.store.materialize(document.object_ref, f"documents/{cache_key}.pdf")
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\assessment\services\assessment_version_publisher.py:177:        object_ref, sha256 = ObjectStore().put_json(key, bundle)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:451:                ApplicationLifecycleCommands(context.db).store.delete_object(object_ref)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:455:    context.after_commit(cleanup)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\job_router.py:83:                lambda object_ref=document.object_ref: service.uploads.store.delete_object(object_ref)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\service.py:148:            object_ref, _ = self.store.put_bytes(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:117:                lambda object_ref=object_ref: service.store.delete_object(object_ref)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\parsing\vision_llm_provider.py:56:        self._activity = activity or ExternalActivity()
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:104:        object_ref, stored_sha256 = self.store.put_bytes(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\document_upload_service.py:182:        object_ref, stored_sha256 = self.store.put_bytes(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\job_document_import_workflow.py:60:    path = ObjectStore().materialize(object_ref, f"documents/{document_id}/{filename}")
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\job_document_import_workflow.py:103:    blocks_ref, blocks_sha256 = ObjectStore().put_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_router.py:374:                lambda object_ref=document.object_ref: service.uploads.store.delete_object(object_ref)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\intake_router.py:630:                lambda object_ref=document.object_ref: service.uploads.store.delete_object(object_ref)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:123:        path = ObjectStore().materialize(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:179:        blocks_ref, blocks_sha = ObjectStore().put_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:193:        blocks_ref, blocks_sha = ObjectStore().put_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:249:    blocks = ObjectStore().read_json(blocks_ref, f"documents/{document_id}/submissions/{submission_id}/document_blocks_v1.json")
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:260:            path = ObjectStore().materialize(str(source["objectRef"]), f"documents/{document_id}/{source['filename']}")
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:275:            blocks_ref, blocks_sha = ObjectStore().put_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:294:    structure_ref, structure_sha = ObjectStore().put_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\workflows\resume_document_import_workflow.py:367:    structure = ObjectStore().read_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\resume_document_service.py:174:            structure = ObjectStore().read_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\services\resume_document_service.py:246:        structure = ObjectStore().read_json(
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\candidates\replacement_service.py:65:        structure = ObjectStore().read_json(structure_ref, f"documents/{document.source_document_id}/submissions/{submission.resume_submission_id}/resume_structure_v2.json")
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\document_ingestion\parsing\mineru_client.py:96:        self._activity = activity or ExternalActivity()
```

## 5. 旧式服务提交调用

```text
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\db\session.py:13:SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:132:        handler=lambda context: _accounts(db).create_role(context.user, payload, commit=False),
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:148:        handler=lambda context: _accounts(db).update_role(context.user, role_id, payload, commit=False),
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:162:        handler=lambda context: _accounts(db).delete_role(context.user, role_id, commit=False),
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:188:        handler=lambda context: _accounts(db).create_user(context.user, raw, commit=False),
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:204:        handler=lambda context: _accounts(db).update_user(context.user, user_id, payload, commit=False),
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:218:        handler=lambda context: _accounts(db).soft_delete_user(context.user, user_id, commit=False),
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:235:            context.user, user_id, body.password, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:260:            context.user, body.name, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:278:            context.user, department_id, payload, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:294:            context.user, department_id, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:320:            context.user, job_id, payload, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\system_admin\router.py:389:            context.user, run_id, body.reason, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:89:            questions=[item.model_dump() for item in body.questions], commit=False,
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:113:            data=data, template_id=template_id, commit=False, created_object_refs=created,
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:140:            context.user, template_version_id, payload, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:158:            context.user, template_version_id, is_default=body.isDefault, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:174:            context.user, template_id, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:192:            context.user, template_id, is_active=body.isActive, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\interview_guides\router.py:233:            context.user, job_id, body.templateId, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\router.py:134:        user, import_type, commit=False
D:\For studying-or-working\Develop-Project\recruit-system-V1.0\backend\app\modules\jobs\router.py:178:        user, channel, commit=False
```