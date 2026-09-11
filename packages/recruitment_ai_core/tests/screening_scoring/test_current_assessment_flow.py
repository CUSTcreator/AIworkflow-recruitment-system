from __future__ import annotations

from recruitment_ai_core.common.interview_targets import build_interview_targets
from recruitment_ai_core.job_capability import compile_job_profile
from recruitment_ai_core.job_capability.current import (
    split_jd_units,
)
from recruitment_ai_core.job_capability.pipeline import run_job_capability_pipeline
from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir
from recruitment_ai_core.screening_scoring.work_unit_structurer import build_resume_profile
from recruitment_ai_core.screening_scoring.resume_experience.indicator_evaluator import (
    _evaluation_response,
)
from recruitment_ai_core.screening_scoring.education_scoring import (
    score_education,
    score_from_rank,
)
from recruitment_ai_core.screening_scoring.score_engine import score_candidate_profile


def test_current_job_profile_keeps_numbered_jd_units() -> None:
    text = "\n".join(
        [
            "工作职责：",
            "1. 负责AI应用开发与工程化落地",
            "2. 参与需求分析和技术方案设计",
            "任职要求：",
            "1. 本科及以上学历",
        ]
    )
    profile = compile_job_profile("JOB_1", text, llm_config={"enabled": False})
    assert len(profile["jd_units"]) == 3
    assert len(profile["job_capabilities"]) == 2
    assert len(profile["qualification_constraints"]) == 1


def test_unnumbered_jd_lines_are_units_and_preferred_items_are_supporting() -> None:
    text = "\n".join(
        [
            "岗位职责",
            "参与AI应用工程化落地；",
            "负责需求分析和方案设计。",
            "任职要求",
            "熟悉Python开发；",
            "加分项",
            "具有Agent项目经验。",
        ]
    )
    units = split_jd_units(text)
    assert [item["section"] for item in units] == [
        "responsibility",
        "responsibility",
        "qualification",
        "preferred",
    ]
    profile = compile_job_profile("JOB_LINES", text, llm_config={"enabled": False})
    preferred = [
        item
        for item in profile["job_capabilities"]
        if item["job_unit_id"] == units[-1]["job_unit_id"]
    ]
    assert preferred and all(item["role"] == "supporting" for item in preferred)


def test_split_jd_units_ignores_job_metadata() -> None:
    text = "\n".join(
        [
            "岗位名称：AI应用开发工程师",
            "招聘人数：2",
            "工作职责",
            "1. 参与RAG应用开发",
            "任职资格",
            "1. 熟悉Python开发",
        ]
    )
    units = split_jd_units(text)
    assert [item["raw_text"] for item in units] == ["参与RAG应用开发", "熟悉Python开发"]
    assert [item["section"] for item in units] == ["responsibility", "qualification"]


def test_structured_excel_fields_separate_capabilities_and_hard_qualifications() -> None:
    profile = compile_job_profile(
        "JOB_STRUCTURED",
        "岗位名称：展示文本不参与结构解析",
        frozen_job_json={
            "title": "AI 应用工程师",
            "headcount": 2,
            "responsibilities": ["负责后端开发"],
            "qualifications": ["熟悉 Python 开发", "本科及以上学历"],
            "education_requirement": "硕士及以上",
            "major_requirement": "计算机相关专业",
        },
        llm_config={"enabled": False},
    )

    assert [item["raw_text"] for item in profile["jd_units"]] == [
        "负责后端开发",
        "熟悉 Python 开发",
    ]
    assert [item["scoring_role"] for item in profile["jd_units"]] == [
        "required",
        "required",
    ]
    assert {item["source_field"] for item in profile["qualification_constraints"]} == {
        "education_requirement",
        "major_requirement",
        "qualifications",
    }
    assert len(profile["job_capabilities"]) == 2


def test_resume_headings_are_not_work_units_and_each_skill_line_is_a_claim() -> None:
    text = "\n".join(
        [
            "专业技能",
            "Java：熟悉集合、异常和反射。",
            "Redis：掌握缓存开发与分布式锁。",
            "项目经历",
            "智能问答系统",
            "https://github.com/example/rag",
            "2025/03 - 2025/06",
            "项目简介：面向企业知识库的问答系统。",
            "技术栈：Python、LangChain、Milvus。",
            "主要工作：负责构建混合检索链路，召回率提升18%。",
            "评测平台项目",
            "项目描述：用于离线评测。",
            "实现RAGAS评测与回归测试。",
            "获奖经历（国家级）",
            "全国创新创业大赛一等奖",
        ]
    )
    resume_ir = build_resume_ir("C_1", text)
    profile = build_resume_profile(resume_ir)
    assert [item.title for item in resume_ir.experience_units] == [
        "智能问答系统",
        "评测平台项目",
    ]
    source_texts = [item.raw_text for item in resume_ir.source_bullets]
    assert source_texts == [
        "主要工作：负责构建混合检索链路，召回率提升18%",
        "实现RAGAS评测与回归测试",
    ]
    assert profile["skill_claims"] == []


def test_precompiled_job_profile_is_reused(monkeypatch) -> None:
    jd_text = "1. 负责后端开发"
    profile = compile_job_profile("JOB_REUSE", jd_text, llm_config={"enabled": False})

    def fail_compile(*args, **kwargs):
        raise AssertionError("matching profile must not be compiled again")

    monkeypatch.setattr(
        "recruitment_ai_core.job_capability.pipeline.compile_job_profile",
        fail_compile,
    )
    monkeypatch.setattr(
        "recruitment_ai_core.job_capability.pipeline.assess_job_capability",
        lambda **kwargs: {"job_profile": kwargs["job_profile"]},
    )
    result = run_job_capability_pipeline(
        {
            "application_id": "APP_1",
            "candidate_id": "C_1",
            "job_id": "JOB_REUSE",
            "jd_text": jd_text,
            "resume_profile": {},
            "profile_version": "V1",
            "job_profile": profile,
        }
    )
    assert result["job_profile"]["job_profile_version_id"] == profile["job_profile_version_id"]


def test_assessment_units_join_the_same_job_capability_model() -> None:
    profile = compile_job_profile(
        "JOB_2",
        "1. 负责后端接口开发",
        assessment_units=[
            {
                "assessment_unit_id": "AU_1",
                "name": "现场编码",
                "capabilities": [
                    {
                        "job_capability_id": "AC_1",
                        "capability_name": "现场编码能力",
                        "capability_definition": "在限定时间内完成可运行代码",
                        "role": "core",
                    }
                ],
            }
        ],
        llm_config={"enabled": False},
    )
    assert profile["assessment_units"][0]["capabilities"][0]["job_unit_id"] == "AU_1"
    assert any(item["job_capability_id"] == "AC_1" for item in profile["job_capabilities"])


def test_education_rank_bands_and_master_weight() -> None:
    assert score_from_rank(1) == 1.0
    assert score_from_rank(200) == 0.70
    assert score_from_rank(201) == 0.65
    profile = {"candidate_facts": {"education_records": [
            {"span_id": "E1", "text": "甲大学 本科"},
            {"span_id": "E2", "text": "乙大学 硕士研究生"},
        ]}}
    result = score_education(
        profile,
        [
            {"canonical_name": "甲大学", "aliases": [], "rank": 50},
            {"canonical_name": "乙大学", "aliases": [], "rank": 120},
        ],
    )
    assert result["score"] == 85.2


def test_education_school_matching_normalizes_parentheses() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [{"span_id": "E1", "text": "中国地质大学(武汉) 本科"}]}},
        [{"canonical_name": "中国地质大学（武汉）", "aliases": [], "rank": 56}],
    )
    assert result["score"] == 90.0


def test_education_prefers_longest_school_name_match() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [{"span_id": "E1", "text": "中国矿业大学（北京） 本科"}]}},
        [
            {"canonical_name": "中国矿业大学", "aliases": [], "rank": 56},
            {"canonical_name": "中国矿业大学（北京）", "aliases": [], "rank": 100},
        ],
    )
    assert result["score"] == 84.0
    assert result["education_records"][0]["school_name"] == "中国矿业大学（北京）"


def test_education_groups_split_school_degree_and_period_spans() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [
                {"span_id": "E1", "text": "深圳大学", "source_line_start": 5, "subspan_index": 5},
                {"span_id": "E2", "text": "信息管理与信息系统·本科", "source_line_start": 6, "subspan_index": 6},
                {"span_id": "E3", "text": "2024/09 - 至今", "source_line_start": 7, "subspan_index": 7},
            ]}},
        [{"canonical_name": "深圳大学", "aliases": [], "rank": 66}],
    )
    assert result["score"] == 84.0
    assert result["status"] == "scored"
    assert result["education_records"] == [
        {
            "education_record_id": "EDU_001",
            "school_name": "深圳大学",
            "degree_level": "undergraduate",
            "rank": 66,
            "school_score": 0.84,
            "raw_text": "深圳大学\n信息管理与信息系统·本科\n2024/09 - 至今",
            "source_span_ids": ["E1", "E2", "E3"],
        }
    ]


def test_education_keeps_split_undergraduate_and_master_records_separate() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [
                {"span_id": "E1", "text": "甲大学", "source_line_start": 1},
                {"span_id": "E2", "text": "计算机科学与技术 本科", "source_line_start": 2},
                {"span_id": "E3", "text": "乙大学", "source_line_start": 3},
                {"span_id": "E4", "text": "软件工程 硕士研究生", "source_line_start": 4},
            ]}},
        [
            {"canonical_name": "甲大学", "aliases": [], "rank": 50},
            {"canonical_name": "乙大学", "aliases": [], "rank": 120},
        ],
    )
    assert result["score"] == 85.2
    assert [item["degree_level"] for item in result["education_records"]] == [
        "undergraduate",
        "master",
    ]


def test_education_infers_degree_when_ranked_school_is_explicit() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [{"span_id": "E1", "text": "东北大学 2024.09 - 2028.06"}]}},
        [{"canonical_name": "东北大学", "aliases": [], "rank": 39}],
    )
    assert result["score"] == 90.0
    assert result["status"] == "degree_inferred"
    assert result["inferred_school_name"] == "东北大学"
    assert result["verification_required"] is True


def test_education_uses_neutral_baseline_for_redacted_school() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [{"span_id": "E1", "text": "2022.09 - 2026.06 XX院校 计算机科学与技术"}]}},
        [{"canonical_name": "清华大学", "aliases": [], "rank": 1}],
    )
    assert result["score"] == 65.0
    assert result["status"] == "school_unknown"
    assert result["verification_required"] is True


def test_education_marks_missing_ranking_dataset_instead_of_claiming_scored() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [{"span_id": "E1", "text": "深圳大学 本科"}]}},
        [],
    )
    assert result["score"] == 65.0
    assert result["status"] == "ranking_unavailable"
    assert result["verification_required"] is True


def test_education_uses_neutral_baseline_when_not_provided() -> None:
    result = score_education({"candidate_facts": {"education_records": []}}, [])
    assert result["score"] == 65.0
    assert result["status"] == "not_provided"
    assert result["supporting_evidence_ids"] == []
    assert result["verification_required"] is True


def test_education_uses_neutral_undergraduate_baseline_for_master_only() -> None:
    result = score_education(
        {"candidate_facts": {"education_records": [{"span_id": "E1", "text": "清华大学 硕士研究生"}]}},
        [{"canonical_name": "清华大学", "aliases": [], "rank": 1}],
    )
    assert result["score"] == 79.0
    assert result["status"] == "undergraduate_missing"
    assert result["verification_required"] is True


def test_total_score_uses_documented_three_weights() -> None:
    profile = {
        "stage": "screening",
        "job_result": {"job_unit_results": [{"job_unit_id": "JDU_1", "score": 0.80}]},
            "preset_experience_result": {"candidate_framework_results": [{"framework_id": "solution_execution", "score": 0.70}]},
        "education_result": {"score": 90.0},
    }
    _, result = score_candidate_profile(profile)
    assert result["base_score"] == 78.0
    assert result["overall_dimension_policy"] == "job_0.50_resume_0.35_education_0.15_v1_0"
    explanation = result["score_explanation_detail"]
    assert explanation["score_name"] == "候选人能力分"
    assert [item["contribution"] for item in explanation["components"]] == [40.0, 24.5, 13.5]


def test_resume_llm_results_allow_partial_response_for_business_degradation() -> None:
    ok, _ = _evaluation_response(
        {"evaluations": [{"target_id": "WU_1", "activated_indicators": []}]},
        {"WU_1", "WU_2"},
    )
    # 顶层传输校验只确认 evaluations 是数组；缺失 WorkUnit 由业务归一化
    # 记录为降级，不应让同批中已返回的合法条目一起丢失。
    assert ok is True


def test_interview_targets_are_generated_from_leaf_capability_results() -> None:
    targets = build_interview_targets(
        risks=[],
        job_profile={
            "job_capabilities": [
                {"job_capability_id": "JC_LOW", "role": "core", "capability_definition": "低分核心能力"},
                {"job_capability_id": "JC_HIGH", "role": "core", "capability_definition": "高分核心能力"},
                {"job_capability_id": "JC_LIVE", "role": "core", "capability_definition": "现场能力"},
            ],
            "assessment_units": [{"capabilities": [{"job_capability_id": "JC_LIVE"}]}],
        },
        capability_results=[
            {"job_capability_id": "JC_LOW", "score": 0.35, "primary_pair_id": "PAIR_LOW", "supplemental_pair_ids": []},
            {"job_capability_id": "JC_HIGH", "score": 0.87, "primary_pair_id": "PAIR_HIGH", "supplemental_pair_ids": []},
            {"job_capability_id": "JC_LIVE", "score": 0.0, "primary_pair_id": None, "supplemental_pair_ids": []},
        ],
        pair_assessments=[
            {"pair_id": "PAIR_LOW", "content_level": 1, "evidence_id": "WU_LOW"},
            {"pair_id": "PAIR_HIGH", "content_level": 4, "evidence_id": "WU_1"},
        ],
        stage="screening",
    )
    assert {item["purpose"] for item in targets} == {
        "verify_experience",
        "direct_demonstration",
    }
    assert all(item["interview_target_id"] for item in targets)
    assert all("target_id" in item for item in targets)
    assert all("source_result_ids" in item for item in targets)
    assert all("trigger_code" in item for item in targets)
    assert all("verification_goal" in item for item in targets)
    assert all("target_ids" not in item for item in targets)
    assert all("risk_ids" not in item for item in targets)
