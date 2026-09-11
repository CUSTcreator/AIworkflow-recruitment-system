from __future__ import annotations

from recruitment_ai_core.job_capability import compile_job_profile
from recruitment_ai_core.screening_scoring import run_screening_scoring
from recruitment_ai_core.screening_scoring.contracts import ScorableWorkUnit
from recruitment_ai_core.screening_scoring.result_contracts import ScoringCoreInput
from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir
from recruitment_ai_core.screening_scoring.work_unit_structurer import build_resume_profile


def _resume_profile(candidate_id: str, text: str) -> dict:
    resume_ir = build_resume_ir(candidate_id, text)
    resume_ir.scorable_work_units = [
        ScorableWorkUnit(
            work_unit_id=f"{bullet.experience_unit_id}_WU_{index:03d}",
            project_id=bullet.experience_unit_id,
            source_refs=[{"bullet_id": bullet.source_bullet_id, "quote": bullet.raw_text}],
        )
        for index, bullet in enumerate(resume_ir.source_bullets, start=1)
    ]
    return build_resume_profile(resume_ir)


def test_screening_uses_frozen_profiles_and_profile_score_engine() -> None:
    resume_profile = _resume_profile(
        "CAND_PROFILE",
        "项目经历\nAI招聘系统\n负责开发后端 API、工作流状态机和幂等提交，并完成测试验证。",
    )
    job_profile = compile_job_profile(
        "JOB_PROFILE",
        "岗位职责\n负责 Agent 工作流、后端 API、状态流转、幂等和评测指标。\n"
        "任职要求\n熟悉 Python 后端和 LLM 调用。\n本科以上学历。",
        llm_config={"enabled": False},
    )

    result = run_screening_scoring(
        ScoringCoreInput(
            resume_profile=resume_profile,
            job_profile=job_profile,
            education_ranking_entries=[],
            ranking_dataset_version="test",
            llm_config={"enabled": False},
        )
    )

    assert result.job_profile["job_capabilities"]
    assert result.job_profile["qualification_constraints"]
    assert result.resume_profile["experience_units"]
    assert "source_bullets" not in result.resume_profile
    assert "scorable_work_units" not in result.resume_profile
    assert len(result.preset_experience_result["candidate_framework_results"]) == 3
    assert result.job_result["job_unit_results"]
    assert all(item["score"] == 0 for item in result.job_result["job_unit_results"])
    assert result.education_result["score"] == 65.0
    assert result.score_result["education_background_score"] == 65.0
    assert result.capability_graph["resume_profile_version_id"] == resume_profile["resume_profile_version_id"]
    assert result.score_result["score"] >= 0
