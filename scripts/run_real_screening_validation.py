from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from recruitment_ai_core.job_capability import compile_job_profile
from recruitment_ai_core.resume_structuring import structure_resume
from recruitment_ai_core.screening_scoring import run_screening_scoring


DATASET_DIR = Path(r"D:\For studying-or-working\Develop-Project\简历数据集")
JD_DIR = Path(r"D:\For studying-or-working\Develop-Project\对应JD")
DEFAULT_JOBS = ("ai应用开发", "后端开发")
RANKING_PATH = Path("backend/app/seeds/data/softke_bcur_2026.json")


def _ranking_entries() -> list[dict[str, Any]]:
    payload = json.loads(RANKING_PATH.read_text(encoding="utf-8"))
    return [
        {
            "canonical_name": item["canonical_name"],
            "aliases": list(item.get("aliases") or []),
            "rank": int(item["rank"]),
        }
        for item in payload["entries"]
    ]


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


def _summary(job_name: str, resume_path: Path, result: dict[str, Any], seconds: float) -> dict[str, Any]:
    bundle = result.get("analysis_bundle") or {}
    profile = bundle.get("candidate_capability_profile") or {}
    experience = bundle.get("resume_experience_assessment") or {}
    job_result = bundle.get("job_capability_assessment") or {}
    job_profile = job_result.get("job_profile") or bundle.get("job_profile") or {}
    projects = experience.get("project_experience_assessments") or []
    score = profile.get("score_summary") or {}
    workflow_errors = result.get("errors") or bundle.get("errors") or []
    if workflow_errors or not score:
        return {
            "job": job_name,
            "candidate": resume_path.stem,
            "status": "failed",
            "error": str(workflow_errors[0] if workflow_errors else "missing_score_summary")[:500],
            "elapsed_seconds": round(seconds, 2),
        }
    return {
        "job": job_name,
        "candidate": resume_path.stem,
        "status": "ok",
        "total_score": score.get("base_score"),
        "job_score": score.get("job_capability_fit_score") or score.get("job_requirement_score"),
        "resume_score": score.get("resume_experience_score"),
        "education_score": score.get("education_background_score"),
        "work_unit_count": sum(len(item.get("work_units") or []) for item in projects),
        "project_evidence_count": sum(
            1 for item in projects if item.get("project_evidence")
        ),
        "active_indicator_count": (experience.get("score_summary") or {}).get("active_indicator_count"),
        "job_capability_count": len(job_profile.get("job_capabilities") or []),
        "gap_count": len(profile.get("evidence_gaps") or []),
        "risk_count": len(profile.get("risks") or []),
        "interview_target_count": len(profile.get("interview_targets") or []),
        "llm_call_count": _call_count(result),
        "degraded": bool(experience.get("degraded") or job_result.get("degraded")),
        "validation_error_count": len(experience.get("validation_errors") or []),
        "elapsed_seconds": round(seconds, 2),
    }


def run_case(job_name: str, resume_path: Path, output_dir: Path, rankings: list[dict[str, Any]], job_profile: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        resume_structure = structure_resume(
            candidate_id=resume_path.stem,
            resume_text=resume_path.read_text(encoding="utf-8"),
            llm_config=None,
        )
        result = run_screening_scoring(
            {
                "application_id": f"VALIDATE_{job_name}_{resume_path.stem}",
                "candidate_id": resume_path.stem,
                "job_id": f"JOB_{job_name}",
                "resume_structure": resume_structure,
                "jd_text": (JD_DIR / f"{job_name}.txt").read_text(encoding="utf-8"),
                "metadata": {
                    "education_ranking_entries": rankings,
                    "job_profile": job_profile,
                },
            }
        )
        seconds = time.perf_counter() - started
        (output_dir / f"{job_name}_{resume_path.stem}_full.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return _summary(job_name, resume_path, result, seconds)
    except Exception as exc:
        return {
            "job": job_name,
            "candidate": resume_path.stem,
            "status": "failed",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", nargs="+", default=list(DEFAULT_JOBS))
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    output_dir = Path(args.output_dir or f"validation_results/{datetime.now():%Y%m%d_%H%M%S}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rankings = _ranking_entries()
    summaries: list[dict[str, Any]] = []
    for job_name in args.jobs:
        jd_text = (JD_DIR / f"{job_name}.txt").read_text(encoding="utf-8")
        job_profile = compile_job_profile(f"JOB_{job_name}", jd_text)
        resumes = sorted(DATASET_DIR.glob(f"{job_name}*.txt"))
        if args.offset > 0:
            resumes = resumes[args.offset:]
        if args.limit > 0:
            resumes = resumes[: args.limit]
        for resume_path in resumes:
            print(f"START {job_name} {resume_path.stem}", flush=True)
            summary = run_case(job_name, resume_path, output_dir, rankings, job_profile)
            summaries.append(summary)
            print("RESULT " + json.dumps(summary, ensure_ascii=False), flush=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OUTPUT_DIR {output_dir.resolve()}", flush=True)
    return 0 if all(item["status"] == "ok" for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
