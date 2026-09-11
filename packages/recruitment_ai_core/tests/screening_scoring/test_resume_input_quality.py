from __future__ import annotations

import pytest

from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir
from recruitment_ai_core.screening_scoring.contracts import ScorableWorkUnit
from recruitment_ai_core.screening_scoring.pipeline import run_screening_scoring
from recruitment_ai_core.screening_scoring.result_contracts import ScoringCoreInput
from recruitment_ai_core.screening_scoring.resume_input_quality import (
    ResumeInputQualityError,
    _title_contains_action,
)
from recruitment_ai_core.screening_scoring.work_unit_structurer import build_resume_profile
from recruitment_ai_core.screening_scoring.resume_experience.preprocessing import prepare_project
from recruitment_ai_core.screening_scoring.resume_experience.pipeline import assess_resume_experience


def test_project_title_role_label_is_not_treated_as_work_fact() -> None:
    assert not _title_contains_action(
        "AgenticRAG 知识库检索｜独立完成 2025.11 - 2025.12"
    )
    assert _title_contains_action(
        "负责搭建 AgenticRAG 知识库检索系统 2025.11 - 2025.12"
    )


def test_project_context_is_preserved_but_not_scorable() -> None:
    text = "\n".join(
        [
            "项目经历",
            "智能问答系统",
            "2024.03-2024.08",
            "项目简介：企业知识库问答系统",
            "技术栈：Python、LangChain、Milvus",
            "开发环境：Linux、Docker",
            "负责构建混合检索链路并完成离线评测，Top-5召回率提升18%。",
        ]
    )
    resume_ir = build_resume_ir("C_CONTEXT", text)
    resume_ir.scorable_work_units = [
        ScorableWorkUnit(
            work_unit_id=f"{bullet.experience_unit_id}_WU_{index:03d}",
            project_id=bullet.experience_unit_id,
            source_refs=[{"bullet_id": bullet.source_bullet_id, "quote": bullet.raw_text}],
        )
        for index, bullet in enumerate(resume_ir.source_bullets, start=1)
    ]
    profile = build_resume_profile(resume_ir)
    project = profile["experience_units"][0]
    context_types = {item["context_type"] for item in project["context_items"]}
    assert {
        "project_title",
        "project_date",
        "project_description",
        "tech_stack",
        "development_environment",
    } <= context_types
    assert [item["raw_text"] for item in project["work_units"]] == [
        "负责构建混合检索链路并完成离线评测，Top-5召回率提升18%"
    ]
    assert profile["resume_profile_version"] == "resume_profile_v1_2"
    assert profile["input_quality_report"]["status"] == "pass"
    assert "source_bullets" not in profile
    assert "scorable_work_units" not in profile
    assert "skill_statements" not in profile
    assert "project_context_items" not in profile


def test_experience_assessment_skips_empty_background_unit() -> None:
    units, degraded, errors = prepare_project(
        {
            "experience_unit_id": "P_1",
            "project_context": [],
            "source_bullets": [{"source_bullet_id": "B_1", "scorable_work_units": []}],
        },
        None,
    )
    assert units == []
    assert degraded is True
    assert errors == ["scoring_skipped_no_work_units:P_1"]


def test_experience_scoring_continues_after_empty_background_unit() -> None:
    result = assess_resume_experience(
        {
            "experience_units": [
                {
                    "experience_unit_id": "P_EMPTY",
                    "title": "实习背景",
                    "project_context": [],
                    "source_bullets": [],
                    "work_units": [],
                },
                {
                    "experience_unit_id": "P_VALID",
                    "title": "有效项目",
                    "project_context": [],
                    "source_bullets": [],
                    "work_units": [
                        {
                            "work_unit_id": "P_VALID_WU_001",
                            "source_refs": [{"bullet_id": "B_2", "quote": "负责完成机械结构设计"}],
                        }
                    ],
                },
            ]
        },
        llm_config={"enabled": False},
    )
    assert result["degraded"] is True
    assert result["skipped_experience_units"] == [
        {
            "experience_unit_id": "P_EMPTY",
            "title": "实习背景",
            "reason_code": "no_scorable_work_units",
            "message": "该段经历未包含可验证的工作事实，未纳入经历能力评分。",
        }
    ]
    assert [item["project_id"] for item in result["project_experience_assessments"]] == [
        "P_EMPTY",
        "P_VALID",
    ]


def test_common_rpa_whitespace_noise_keeps_project_and_fact() -> None:
    text = (
        "专业技能\r\n"
        "\tJava：熟悉集合、异常和反射。\r\n"
        "项目经历\r\n"
        "智能问答系统\r\n"
        "2024.03—2024.08\r\n"
        "• 负责构建检索链路；召回率提升18%。"
    )
    resume_ir = build_resume_ir("C_NOISE", text)
    assert len(resume_ir.experience_units) == 1
    assert resume_ir.experience_units[0].title == "智能问答系统"
    assert len(resume_ir.source_bullets) == 1
    assert "召回率提升18%" in resume_ir.source_bullets[0].raw_text
    assert resume_ir.input_quality_report is not None
    assert resume_ir.input_quality_report.status != "reject"


def test_long_single_line_and_action_in_project_title_are_not_silent() -> None:
    long_text = "项目经历 智能问答系统 负责构建检索链路并完成离线评测。" * 25
    long_report = build_resume_ir("C_LONG", long_text).input_quality_report
    assert long_report is not None
    assert long_report.suspicious_long_line_ratio > 0
    assert long_report.status != "pass"

    title_with_fact = "\n".join(
        [
            "项目经历",
            "智能问答系统 2024.03-2024.08 负责构建检索链路并提升召回率18%",
        ]
    )
    title_report = build_resume_ir("C_TITLE_FACT", title_with_fact).input_quality_report
    assert title_report is not None
    assert "project_title_may_contain_work_fact" in title_report.warnings
    assert title_report.status == "warning"


def test_severe_ocr_damage_is_rejected_but_light_damage_is_only_warning() -> None:
    light = "\n".join(
        [
            "项目经历",
            "智能问答系统",
            "负责构建检索链路并完成离线评测，召回率提升18%�。",
        ]
    )
    severe = "项目经历\n" + ("�\x00" * 100)
    light_report = build_resume_ir("C_LIGHT_OCR", light).input_quality_report
    severe_report = build_resume_ir("C_SEVERE_OCR", severe).input_quality_report
    assert light_report is not None and severe_report is not None
    assert light_report.status == "warning"
    assert severe_report.status == "reject"
    assert severe_report.garbled_character_ratio > light_report.garbled_character_ratio


def test_raw_text_cannot_bypass_formal_resume_structure() -> None:
    with pytest.raises(ValueError, match="frozen_resume_profile_required"):
        run_screening_scoring(
            ScoringCoreInput(
                resume_profile={},
                job_profile={},
                education_ranking_entries=[],
                ranking_dataset_version="test",
                llm_config={"enabled": False},
            )
        )


def test_duplicate_lines_are_reported() -> None:
    text = "\n".join(
        [
            "项目经历",
            "智能问答系统",
            "负责构建检索链路并完成离线评测。",
            "负责构建检索链路并完成离线评测。",
            "负责构建检索链路并完成离线评测。",
        ]
    )
    report = build_resume_ir("C_DUPLICATE", text).input_quality_report
    assert report is not None
    assert report.duplicate_line_ratio > 0
    assert "duplicate_lines" in report.warnings
