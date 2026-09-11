from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = Path(r"D:\For studying-or-working\Develop-Project\简历数据集")
JD_DIR = Path(r"D:\For studying-or-working\Develop-Project\对应JD")
DEFAULT_JOBS = ("ai应用开发", "后端开发")


def _prepare_environment(output_dir: Path) -> None:
    database_path = (output_dir / "worker_validation.db").resolve()
    object_store_path = (output_dir / "object_store").resolve()
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    os.environ["LOCAL_OBJECT_STORE_DIR"] = str(object_store_path)
    os.environ["ALLOW_STORAGE_FALLBACK"] = "1"
    os.environ.setdefault("WORKFLOW_WORKER_LEASE_SECONDS", "600")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    package_root = ROOT / "packages" / "recruitment_ai_core"
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))


def _call_count(value: Any) -> int:
    call_ids: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            call_id = item.get("call_id")
            if isinstance(call_id, str) and call_id:
                call_ids.add(call_id)
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return len(call_ids)


def _top_resume_capabilities(profile: dict[str, Any], limit: int = 3) -> list[str]:
    rows = sorted(
        profile.get("demonstrated_capability_assessments") or [],
        key=lambda item: float(item.get("candidate_score") or 0),
        reverse=True,
    )
    return [
        f"{item.get('indicator_name') or item.get('indicator_id')} L{item.get('current_validated_level', 0)}"
        for item in rows[:limit]
    ]


def _top_job_capabilities(profile: dict[str, Any], limit: int = 3) -> list[str]:
    rows = sorted(
        profile.get("requirement_assessments") or [],
        key=lambda item: float(item.get("score") or 0),
        reverse=True,
    )
    return [
        f"{item.get('capability_name') or item.get('job_capability_id')} {round(float(item.get('score') or 0) * 100, 1)}"
        for item in rows[:limit]
    ]


def _input_quality(bundle: dict[str, Any]) -> dict[str, Any]:
    report = (bundle.get("resume_profile") or {}).get("input_quality_report") or {}
    return {
        "status": report.get("status") or report.get("quality_level") or "unknown",
        "issue_count": len(report.get("issues") or report.get("warnings") or []),
    }


def _summary(
    *,
    job_name: str,
    candidate_name: str,
    result: dict[str, Any],
    elapsed_seconds: float,
    run: Any,
    persistence: dict[str, Any],
) -> dict[str, Any]:
    bundle = result.get("analysis_bundle") or {}
    profile = bundle.get("candidate_capability_profile") or {}
    experience = bundle.get("resume_experience_assessment") or {}
    job_result = bundle.get("job_capability_assessment") or {}
    projects = experience.get("project_experience_assessments") or []
    score = profile.get("score_summary") or {}
    quality = _input_quality(bundle)
    return {
        "job": job_name,
        "candidate": candidate_name,
        "status": run.status,
        "total_score": score.get("base_score"),
        "job_score": score.get("job_capability_fit_score") or score.get("job_requirement_score"),
        "resume_score": score.get("resume_experience_score"),
        "education_score": score.get("education_background_score"),
        "project_count": len(projects),
        "work_unit_count": sum(len(item.get("work_units") or []) for item in projects),
        "project_evidence_count": sum(
            1 for item in projects if item.get("project_evidence")
        ),
        "active_indicator_count": (experience.get("score_summary") or {}).get("active_indicator_count"),
        "active_domain_count": (experience.get("score_summary") or {}).get("active_domain_count"),
        "job_capability_count": len((bundle.get("job_profile") or {}).get("job_capabilities") or []),
        "gap_count": len(profile.get("evidence_gaps") or []),
        "risk_count": len(profile.get("risks") or []),
        "interview_target_count": len(profile.get("interview_targets") or []),
        "top_resume_capabilities": _top_resume_capabilities(profile),
        "top_job_capabilities": _top_job_capabilities(profile),
        "input_quality": quality["status"],
        "input_quality_issue_count": quality["issue_count"],
        "degraded": bool(experience.get("degraded") or job_result.get("degraded")),
        "llm_call_count": _call_count(result),
        "worker_attempt_count": run.attempt_count,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "persistence": persistence,
    }


def _persistence_counts(db: Any, application_id: str, workflow_run_id: str) -> dict[str, Any]:
    from sqlalchemy import func, select

    from backend.app.models.entities import (
        Application,
        CandidateCapabilityProfileRecord,

        JobRequirementProfileRecord,
        RequirementAssessment,
        ResumeProfileRecord,
        Risk,
        ScoreSnapshot,
        ScreeningAssessment,
        VerificationTarget,
        WorkflowArtifact,
    )

    def count(model: Any, condition: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(condition)) or 0)

    profile = db.scalar(
        select(CandidateCapabilityProfileRecord)
        .where(CandidateCapabilityProfileRecord.application_id == application_id)
        .order_by(CandidateCapabilityProfileRecord.created_at.desc())
    )
    return {
        "workflow_artifact_count": count(
            WorkflowArtifact,
            WorkflowArtifact.workflow_run_id == workflow_run_id,
        ),
        "score_snapshot_count": count(ScoreSnapshot, ScoreSnapshot.application_id == application_id),
        "screening_assessment_count": count(
            ScreeningAssessment,
            ScreeningAssessment.application_id == application_id,
        ),
        "requirement_assessment_count": count(
            RequirementAssessment,
            RequirementAssessment.application_id == application_id,
        ),
        "risk_count": count(Risk, Risk.application_id == application_id),
        "verification_target_count": count(
            VerificationTarget,
            VerificationTarget.application_id == application_id,
        ),
        "resume_profile_count": count(
            ResumeProfileRecord,
            ResumeProfileRecord.candidate_id
            == db.get(Application, application_id).candidate_id,
        ),
        "capability_profile_count": count(
            CandidateCapabilityProfileRecord,
            CandidateCapabilityProfileRecord.application_id == application_id,
        ),
        "profile_links_valid": bool(
            profile

            and db.get(ResumeProfileRecord, profile.resume_profile_id)
            and db.get(JobRequirementProfileRecord, profile.job_profile_id)
        ),
    }


def _latest_screening_result(db: Any, application_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from backend.app.models.entities import WorkflowArtifact

    artifact = db.scalar(
        select(WorkflowArtifact)
        .where(
            WorkflowArtifact.application_id == application_id,
            WorkflowArtifact.artifact_type == "screening_result",
        )
        .order_by(WorkflowArtifact.created_at.desc())
    )
    return dict(artifact.artifact_json or {}) if artifact else {}


def _rebind_job(application_ids: list[str], shared_job_id: str) -> None:
    from backend.app.db.session import SessionLocal
    from backend.app.models.entities import Application

    with SessionLocal() as db:
        for application_id in application_ids:
            application = db.get(Application, application_id)
            if application is None:
                raise RuntimeError(f"application_missing:{application_id}")
            application.job_id = shared_job_id
        db.commit()


def _validate_run(db: Any, application_id: str, workflow_run_id: str) -> tuple[Any, dict[str, Any]]:
    from backend.app.models.entities import Application, Task, WorkflowArtifact, WorkflowRun
    from sqlalchemy import select

    run = db.get(WorkflowRun, workflow_run_id)
    application = db.get(Application, application_id)
    if run is None or application is None:
        raise RuntimeError("workflow_or_application_missing")
    if run.status != "completed":
        raise RuntimeError(f"workflow_{run.status}:{run.error_message or 'unknown_error'}")
    if run.error_message or run.lease_owner or run.lease_expires_at or run.completed_at is None:
        raise RuntimeError("workflow_completion_fields_invalid")
    if application.status != "department_review":
        raise RuntimeError(f"application_status_invalid:{application.status}")
    artifact_types = set(
        db.scalars(
            select(WorkflowArtifact.artifact_type).where(
                WorkflowArtifact.workflow_run_id == workflow_run_id
            )
        ).all()
    )
    required = {"analysis_bundle", "screening_result", "ai_decision_summary"}
    if not required.issubset(artifact_types):
        raise RuntimeError(f"workflow_artifacts_missing:{sorted(required - artifact_types)}")
    tasks = list(db.scalars(select(Task).where(Task.application_id == application_id)).all())
    task_states = {(item.task_type, item.status) for item in tasks}
    if ("run_scoring", "done") not in task_states or ("department_review", "pending") not in task_states:
        raise RuntimeError(f"task_states_invalid:{sorted(task_states)}")
    persistence = _persistence_counts(db, application_id, workflow_run_id)
    if not persistence["profile_links_valid"]:
        raise RuntimeError("screening_profile_links_invalid")
    return run, persistence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", nargs="+", default=list(DEFAULT_JOBS))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    output_dir = Path(args.output_dir or ROOT / "validation_results" / f"worker_v4_{datetime.now():%Y%m%d_%H%M%S}")
    output_dir.mkdir(parents=True, exist_ok=False)
    _prepare_environment(output_dir)

    from fastapi.testclient import TestClient

    from backend.app.db.session import SessionLocal
    from backend.app.main import app
    from backend.app.models.entities import WorkflowRun
    from backend.app.seeds.import_university_rankings import import_university_rankings
    from backend.app.workers.workflow_worker import process_next_workflow_run

    summaries: list[dict[str, Any]] = []
    with TestClient(app) as client:
        with SessionLocal() as db:
            import_university_rankings(db)
        login = client.post("/api/v1/auth/login", json={"username": "hr", "password": "Hr@123456"})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}

        imported: dict[str, list[dict[str, str]]] = {}
        for job_index, job_name in enumerate(args.jobs, start=1):
            jd_path = JD_DIR / f"{job_name}.txt"
            resume_paths = sorted(DATASET_DIR.glob(f"{job_name}*.txt"))[: args.limit]
            if not jd_path.is_file() or not resume_paths:
                raise FileNotFoundError(f"validation_inputs_missing:{job_name}")
            rows: list[dict[str, str]] = []
            for candidate_index, resume_path in enumerate(resume_paths, start=1):
                suffix = f"{job_index:02d}_{candidate_index:02d}"
                body = {
                    "application_id": f"VAL_APP_{suffix}",
                    "candidate_id": f"VAL_CAND_{suffix}",
                    "job_id": f"VAL_JOB_{suffix}",
                    "candidate_name": resume_path.stem,
                    "job_title": job_name,
                    "department_id": "DEPT_TECH",
                    "resume_text": resume_path.read_text(encoding="utf-8"),
                    "jd_text": jd_path.read_text(encoding="utf-8"),
                }
                response = client.post("/api/v1/applications", json=body, headers=headers)
                response.raise_for_status()
                rows.append(
                    {
                        "application_id": body["application_id"],
                        "candidate_name": resume_path.stem,
                        "job_id": body["job_id"],
                    }
                )
            imported[job_name] = rows
            _rebind_job([item["application_id"] for item in rows], rows[0]["job_id"])

        cases = [
            (job_name, item)
            for job_name, rows in imported.items()
            for item in rows
        ]
        if args.smoke_only:
            cases = cases[:1]

        for case_index, (job_name, case) in enumerate(cases, start=1):
            application_id = case["application_id"]
            print(f"START {case_index}/{len(cases)} {job_name} {case['candidate_name']}", flush=True)
            started = time.perf_counter()
            enqueue = client.post(
                f"/api/v1/applications/{application_id}/workflows/scoring/run",
                json={},
                headers={**headers, "Idempotency-Key": f"validation-scoring-{application_id}"},
            )
            enqueue.raise_for_status()
            workflow_run_id = enqueue.json()["workflow_run_id"]
            if not process_next_workflow_run("validation-worker"):
                raise RuntimeError("worker_did_not_claim_workflow")
            elapsed_seconds = time.perf_counter() - started
            with SessionLocal() as db:
                run, persistence = _validate_run(db, application_id, workflow_run_id)
                result = _latest_screening_result(db, application_id)
                summary = _summary(
                    job_name=job_name,
                    candidate_name=case["candidate_name"],
                    result=result,
                    elapsed_seconds=elapsed_seconds,
                    run=run,
                    persistence=persistence,
                )
            result_view = client.get(
                f"/api/v1/applications/{application_id}/screening-result",
                headers=headers,
            )
            result_view.raise_for_status()
            (output_dir / f"{application_id}_full.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summaries.append(summary)
            (output_dir / "summary.json").write_text(
                json.dumps(summaries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print("RESULT " + json.dumps(summary, ensure_ascii=False), flush=True)

    print(f"OUTPUT_DIR {output_dir.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
