from recruitment_ai_core.common.scoring_evidence import build_scoring_evidence
import recruitment_ai_core.job_capability.current as job_capability_current
from recruitment_ai_core.job_capability.current import (
    _aggregate_capabilities,
    _aggregate_job,
    _core_score,
    _evidence_results,
    extract_job_unit_capabilities_batch,
    _score_pair_batch,
    assemble_job_capability_pair_activities,
    fallback_job_capability_pair_activity,
    prepare_job_capability_pair_activities,
)
from recruitment_ai_core.llm.schema_validator import validate_json_schema
from recruitment_ai_core.screening_scoring.resume_experience.aggregation import (
    aggregate_project_indicators,
    aggregate_resume_score,
    hierarchical_score,
)
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import (
    get_preset_model,
)
from recruitment_ai_core.screening_scoring.resume_experience.indicator_evaluator import (
    _normalize_project_pao,
    _normalize_project_results,
    _normalize_unit_results,
)


def test_job_pair_activities_are_split_by_evidence_type_and_llm_batch(monkeypatch):
    """每个 Activity 对应一次真实 LLM 批量请求，而非整段岗位能力评分。"""
    monkeypatch.setattr(job_capability_current, "PAIR_BATCH_MAX_ITEMS", 1)
    job_profile = {
        "job_capabilities": [{
            "job_capability_id": "JDC_1", "job_unit_id": "JDU_1", "role": "core",
            "required_evidence_elements": [],
        }],
    }
    evidence = {
        "work_units": [
            {"work_unit_id": "WU_1", "project_id": "P_1", "is_current": True},
            {"work_unit_id": "WU_2", "project_id": "P_2", "is_current": True},
        ],
        "project_evidence": [{"project_id": "P_1", "work_units": []}],
        "skill_claims": [{"skill_claim_id": "SC_1"}],
    }

    activities = prepare_job_capability_pair_activities(job_profile, evidence)

    assert [item["activity_key"] for item in activities] == [
        "job_pair:work_unit:batch:001",
        "job_pair:work_unit:batch:002",
        "job_pair:project_evidence:batch:001",
        "job_pair:skill_claim:batch:001",
    ]
    assert all(len(item["pairs"]) == 1 for item in activities)


def test_one_exhausted_job_pair_batch_does_not_discard_other_batch_results(monkeypatch):
    """一批重试耗尽时，仅该批未评估；已成功批次仍进入确定性聚合。"""
    monkeypatch.setattr(job_capability_current, "PAIR_BATCH_MAX_ITEMS", 1)
    job_profile = {
        "job_capabilities": [{
            "job_capability_id": "JDC_1", "job_unit_id": "JDU_1", "role": "core",
            "required_evidence_elements": [],
        }],
        "jd_units": [{"job_unit_id": "JDU_1", "scoring_role": "required"}],
    }
    activities = prepare_job_capability_pair_activities(job_profile, {
        "work_units": [
            {"work_unit_id": "WU_BAD", "project_id": "P_1", "is_current": True},
            {"work_unit_id": "WU_GOOD", "project_id": "P_2", "is_current": True},
        ],
    })
    outputs = {}
    for item in activities:
        key = item["activity_key"]
        pair = item["pairs"][0]
        if pair["work_unit"]["work_unit_id"] == "WU_BAD":
            outputs[key] = fallback_job_capability_pair_activity(
                pairs=item["pairs"], evidence_type="work_unit", error_message="provider_timeout",
            )
        else:
            outputs[key] = {
                "pair_levels": {pair["pair_id"]: {"content_level": 3, "reason": "有效支持"}},
                "prompt_traces": [],
                "degraded": False,
            }

    result = assemble_job_capability_pair_activities(
        application_id="A_1", job_profile=job_profile,
        profile_version="V1", stage="screening",
        activities=activities, activity_outputs=outputs,
    )

    assert result["degraded"] is True
    assert [item["evidence_id"] for item in result["pair_assessments"]] == ["WU_GOOD"]
    assert result["job_capability_results"][0]["score"] == 0.77
def test_both_preset_models_have_three_frameworks_and_nine_indicators():
    for model_id in ("engineering_experience", "general_professional_experience"):
        model = get_preset_model(model_id)
        assert len(model["frameworks"]) == 3
        assert len(model["indicators"]) == 9
        assert {item["framework_id"] for item in model["indicators"]} == {
            "problem_context", "solution_execution", "outcome_value"
        }

def test_project_indicator_blends_local_and_global():
    unit = [{
        "work_unit_indicator_result_id": "WUIR_1", "work_unit_id": "WU_1",
        "indicator_id": "solution_fit_scale", "level": 3, "score": 0.77,
    }]
    project = [{
        "indicator_id": "solution_fit_scale", "level": 4,
        "work_unit_ids": ["WU_1"], "source_refs": [],
    }]
    result = aggregate_project_indicators("P_1", unit, project)[0]
    assert result["local_result"]["score"] == 0.77
    assert result["project_evaluation"]["score"] == 0.87
    assert result["score"] == 0.83


def test_indicator_scores_do_not_require_llm_evidence_fields():
    """WorkUnit 与项目级指标的来源范围由后端输入固定，不由 LLM 回传。"""
    units = [{
        "work_unit_id": "WU_1",
        "project_id": "P_1",
        "source_refs": [{"bullet_id": "SB_1", "quote": "实现检索链路"}],
    }]
    unit_results, unit_errors = _normalize_unit_results(
        [{"target_id": "WU_1", "activated_indicators": [{
            "indicator_id": "solution_fit_scale", "level": 3, "reason": "能够完成实现",
        }]}],
        units,
        {"solution_fit_scale"},
    )
    project_results, project_errors = _normalize_project_results(
        {"project_id": "P_1", "activated_indicators": [{
            "indicator_id": "solution_fit_scale", "level": 4, "reason": "项目整体完成度较高",
        }]},
        units,
        {"solution_fit_scale"},
    )

    assert unit_errors == []
    assert project_errors == []
    assert unit_results[0]["level"] == 3
    assert "evidence_quotes" not in unit_results[0]
    assert project_results[0]["work_unit_ids"] == ["WU_1"]
    assert "source_refs" not in project_results[0]


def test_only_project_pao_accepts_work_unit_evidence_ids():
    units = [{
        "work_unit_id": "WU_1",
        "project_id": "P_1",
        "source_refs": [{"bullet_id": "SB_1", "quote": "设计并实现检索服务"}],
    }]
    pao, errors = _normalize_project_pao(
        {"problem": {
            "summary": "需要实现检索服务",
            "work_unit_ids": ["WU_1"],
            "evidence_work_unit_ids": ["WU_1"],
        }, "approach": None, "outcome": None},
        "P_1",
        units,
    )

    assert errors == []
    assert pao["problem"]["evidence_work_unit_ids"] == ["WU_1"]
    assert "source_refs" not in pao["problem"]


def test_project_pao_rejects_unknown_evidence_work_unit_id():
    pao, errors = _normalize_project_pao(
        {"problem": {
            "summary": "需要实现检索服务",
            "work_unit_ids": ["WU_1"],
            "evidence_work_unit_ids": ["WU_2"],
        }, "approach": None, "outcome": None},
        "P_1",
        [{"work_unit_id": "WU_1", "project_id": "P_1"}],
    )

    assert pao["problem"] is None
    assert errors == ["project_pao:problem:invalid_reference"]

def test_resume_framework_and_total_formulas():
    assert hierarchical_score([1.0, 0.35]) == 0.922
    score = aggregate_resume_score([
        {"framework_id": "problem_context", "score": 0.6},
        {"framework_id": "solution_execution", "score": 0.8},
        {"framework_id": "outcome_value", "score": 0.7},
    ])
    assert score == 76.15


def test_job_pair_combines_content_and_existing_quality():
    capability = {
        "job_capability_id": "JDC_1",
        "quality_focus_ids": ["mechanism_engineering_depth"],
    }
    evidence = {
        "work_unit_id": "WU_1", "project_id": "P_1",
        "work_unit_indicator_results": [{"indicator_id": "mechanism_engineering_depth", "level": 4, "score": 0.87}],
    }
    pair = {"pair_id": "PAIR_1", "job_capability": capability, "work_unit": evidence}
    result = _evidence_results(
        [pair],
        {"PAIR_1": {"content_level": 3, "matched_element_ids": [], "supporting_source_refs": []}},
        [], {}, [], {},
    )[0]
    assert result["content_score"] == 0.77
    assert result["quality_score"] == 0.87
    assert result["pair_score"] == 0.70994



def test_skill_claim_keeps_content_score_when_capability_has_quality_focus() -> None:
    """技能声明无经历质量指标，不能因此被 quality_focus 系数额外扣分。"""
    capability = {
        "job_capability_id": "JDC_1",
        "quality_focus_ids": ["mechanism_engineering_depth"],
    }
    pair = {
        "pair_id": "PAIR_SC_1",
        "job_capability": capability,
        "skill_claim": {"skill_claim_id": "SC_1", "skill_name": "Python"},
    }
    result = _evidence_results(
        [], {}, [], {}, [pair], {
            "PAIR_SC_1": {
                "content_level": 2,
                "matched_element_ids": [],
                "supporting_source_refs": [],
            }
        },
    )[0]
    assert result["content_score"] == 0.60
    assert result["quality_score"] is None
    assert result["pair_score"] == 0.60


def test_skill_claim_level_is_capped_at_l2() -> None:
    capability = {"job_capability_id": "JDC_1"}
    pair = {
        "pair_id": "PAIR_SC_2",
        "job_capability": capability,
        "skill_claim": {"skill_claim_id": "SC_2", "skill_name": "Python"},
    }
    result = _evidence_results(
        [], {}, [], {}, [pair], {
            "PAIR_SC_2": {
                "content_level": 5,
                "matched_element_ids": [],
                "supporting_source_refs": [],
            }
        },
    )[0]
    assert result["content_level"] == 2
    assert result["content_score"] == 0.60

def _work_unit_pair():
    return {
        "pair_id": "PAIR_1",
        "job_capability": {
            "job_capability_id": "JDC_1",
            "required_evidence_elements": [{"element_id": "E_1"}],
        },
        "work_unit": {
            "work_unit_version_id": "WUV_1",
            "source_refs": [
                {"source_id": "SRC_1", "quote": "构建混合检索并完成评测"}
            ],
        },
    }


def test_job_pair_rehydrates_fixed_identity_and_source_quote(monkeypatch):
    def fake_call_json_llm(**kwargs):
        del kwargs
        # 模型只返回语义判断；固定身份和候选来源均由后端回填。
        return {
            "pairs": [{
                "pair_key": "PAIR_1",
                "content_level": 4,
                "matched_element_ids": ["E_1"],
                "reason": "支持",
                "unexpected_model_field": "must be dropped",
            }]
        }, {"status": "completed"}

    monkeypatch.setattr(job_capability_current, "call_json_llm", fake_call_json_llm)
    results, _, degraded = _score_pair_batch(
        [_work_unit_pair()], "work_unit", {}, {}, "prompt"
    )

    item = results["PAIR_1"]
    assert degraded is False
    assert item["job_capability_id"] == "JDC_1"
    assert item["evidence_type"] == "work_unit"
    assert item["evidence_id"] == "WUV_1"
    assert "unexpected_model_field" not in item
    assert item["supporting_source_refs"] == [{
        "source_id": "SRC_1",
        "work_unit_id": "WUV_1",
        "quote": "构建混合检索并完成评测",
    }]


def test_job_pair_does_not_require_llm_source_reference(monkeypatch):
    def fake_call_json_llm(**kwargs):
        del kwargs
        return {
            "pairs": [{
                "pair_key": "PAIR_1",
                "content_level": 4,
                "matched_element_ids": ["E_1"],
                "reason": "支持",
            }]
        }, {"status": "completed"}

    monkeypatch.setattr(job_capability_current, "call_json_llm", fake_call_json_llm)
    results, _, degraded = _score_pair_batch(
        [_work_unit_pair()], "work_unit", {}, {}, "prompt"
    )

    assert degraded is False
    assert results["PAIR_1"]["content_level"] == 4
    assert results["PAIR_1"]["matched_element_ids"] == ["E_1"]
    assert results["PAIR_1"]["supporting_source_refs"] == [{
        "source_id": "SRC_1",
        "work_unit_id": "WUV_1",
        "quote": "构建混合检索并完成评测",
    }]


def test_job_pair_non_retryable_response_error_degrades_after_two_attempts(monkeypatch):
    calls = 0

    def fake_call_json_llm(**kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        raise RuntimeError("llm_response_schema_invalid")

    monkeypatch.setattr(job_capability_current, "call_json_llm", fake_call_json_llm)
    results, traces, degraded = _score_pair_batch(
        [_work_unit_pair()], "work_unit", {}, {}, "prompt"
    )

    assert calls == 2
    assert degraded is True
    assert results["PAIR_1"]["assessment_status"] == "unassessed"
    assert "content_level" not in results["PAIR_1"]
    assert traces[-1]["status"] == "degraded"


def test_job_pair_retryable_external_error_bubbles_to_step_runner(monkeypatch):
    class RetryableError(RuntimeError):
        retryable = True

    def fake_call_json_llm(**kwargs):
        del kwargs
        raise RetryableError("provider_timeout")

    monkeypatch.setattr(job_capability_current, "call_json_llm", fake_call_json_llm)
    try:
        _score_pair_batch([_work_unit_pair()], "work_unit", {}, {}, "prompt")
    except RetryableError:
        return
    raise AssertionError("可重试外部错误必须交给 StepRunner 处理")


def test_job_uses_top_scores_and_fixed_mean_at_each_level():
    assert _core_score([1.0, 0.35]) == 0.72375
    assert _aggregate_job([
        {"score": 1.0, "aggregation_role": "required"},
        {"score": 0.35, "aggregation_role": "required"},
    ]) == 0.715625


def test_project_evidence_and_member_work_unit_are_not_rewarded_twice():
    capability = {
        "job_capability_id": "JDC_1", "job_unit_id": "JDU_1",
        "capability_name": "工程化", "capability_definition": "工程化", "role": "core",
    }
    rows = [
        {"pair_id": "PAIR_1", "job_capability_id": "JDC_1", "evidence_type": "project", "evidence_id": "P_1",
         "content_level": 4, "pair_score": 0.87, "proof_work_unit_ids": ["WU_1"], "_project_id": "P_1"},
        {"pair_id": "PAIR_2", "job_capability_id": "JDC_1", "evidence_type": "work_unit", "evidence_id": "WU_1",
         "content_level": 3, "pair_score": 0.77, "proof_work_unit_ids": ["WU_1"], "_project_id": "P_1"},
    ]
    result = _aggregate_capabilities([capability], rows)[0]
    assert result["job_unit_id"] == "JDU_1"
    assert result["score"] == 0.87
    assert result["primary_pair_id"] == "PAIR_1"
    assert result["supplemental_pair_ids"] == []


def test_skill_claim_is_a_real_weak_supplement_not_an_explanation_only_item():
    capability = {
        "job_capability_id": "JDC_1", "job_unit_id": "JDU_1",
        "capability_name": "工程化", "capability_definition": "工程化", "role": "core",
    }
    rows = [
        {"pair_id": "PAIR_WU", "job_capability_id": "JDC_1", "evidence_type": "work_unit",
         "evidence_id": "WU_1", "content_level": 3, "pair_score": 0.77,
         "proof_work_unit_ids": ["WU_1"], "_project_id": "P_1"},
        {"pair_id": "PAIR_SC", "job_capability_id": "JDC_1", "evidence_type": "skill_claim",
         "evidence_id": "SC_1", "content_level": 1, "pair_score": 0.35,
         "proof_work_unit_ids": [], "_project_id": None},
    ]
    result = _aggregate_capabilities([capability], rows)[0]
    assert result["score"] == 0.7805
    assert result["primary_pair_id"] == "PAIR_WU"
    assert result["supplemental_pair_ids"] == ["PAIR_SC"]


def test_runtime_scoring_evidence_contains_project_evidence_and_unit_indicators():
    resume_result = {
        "project_experience_assessments": [{
            "project_id": "P_1", "project_name": "项目",
            "work_units": [{"work_unit_id": "WU_1", "project_id": "P_1", "source_refs": []}],
            "work_unit_indicator_results": [{
                "work_unit_id": "WU_1", "indicator_id": "mechanism_engineering_depth", "level": 3
            }],
            "project_evidence": {"project_evidence_id": "PE_1", "project_id": "P_1", "work_unit_ids": ["WU_1"]},
        }]
    }
    scoring_evidence = build_scoring_evidence(
        resume_profile={"resume_profile_version_id": "RP_1", "experience_units": []},
        resume_experience_result=resume_result,
    )
    assert scoring_evidence["project_evidence"][0]["project_evidence_id"] == "PE_1"
    assert scoring_evidence["work_units"][0]["work_unit_indicator_results"][0]["level"] == 3

def test_empty_degraded_project_evidence_is_excluded_from_job_pairing_inputs():
    """重试耗尽的空项目占位结果不能触发岗位能力 LLM 配对。"""
    resume_result = {
        "project_experience_assessments": [
            {
                "project_id": "P_EMPTY",
                "project_evidence": {
                    "project_id": "P_EMPTY",
                    "project_pao_result": None,
                    "project_indicator_results": [],
                    "work_units": [],
                },
            },
            {
                "project_id": "P_VALID",
                "project_evidence": {
                    "project_id": "P_VALID",
                    "project_pao_result": None,
                    "project_indicator_results": [{"indicator_id": "mechanism_engineering_depth"}],
                    "work_units": [],
                },
            },
        ]
    }
    evidence = build_scoring_evidence(
        resume_profile={"resume_profile_version_id": "RP_1", "experience_units": []},
        resume_experience_result=resume_result,
    )
    assert [item["project_id"] for item in evidence["project_evidence"]] == ["P_VALID"]


def test_real_l0_pair_is_not_published_as_pair_assessment():
    """真实无关证据不进入 PairAssessment，也不参与能力证据选择。"""
    result = _evidence_results(
        [_work_unit_pair()],
        {"PAIR_1": {"content_level": 0, "reason": "与岗位能力无关"}},
        [], {}, [], {},
    )
    assert result == []


def test_unassessed_pair_is_not_reinterpreted_as_l0_pair_assessment():
    """LLM 失败只是未评估，不能产生看似真实的零分配对。"""
    result = _evidence_results(
        [_work_unit_pair()],
        {"PAIR_1": job_capability_current._fallback_pair(_work_unit_pair(), "work_unit")},
        [], {}, [], {},
    )
    assert result == []


def test_fixed_job_capability_remains_zero_when_no_valid_pair_exists():
    """丢弃底层 L0 配对后，固定岗位能力仍必须作为零分节点保留。"""
    capabilities = [
        {"job_capability_id": "JDC_MATCHED", "job_unit_id": "JDU_1", "role": "core"},
        {"job_capability_id": "JDC_UNMATCHED", "job_unit_id": "JDU_1", "role": "core"},
    ]
    result = _aggregate_capabilities(capabilities, [{
        "pair_id": "PAIR_MATCHED", "job_capability_id": "JDC_MATCHED",
        "evidence_type": "work_unit", "evidence_id": "WU_1", "content_level": 3,
        "pair_score": 0.77, "proof_work_unit_ids": ["WU_1"], "_project_id": "P_1",
    }])
    by_id = {item["job_capability_id"]: item for item in result}
    assert by_id["JDC_MATCHED"]["score"] == 0.77
    assert by_id["JDC_UNMATCHED"] == {
        "job_capability_result_id": "JCR_JDC_UNMATCHED",
        "job_capability_id": "JDC_UNMATCHED",
        "job_unit_id": "JDU_1",
        "score": 0.0,
        "primary_pair_id": None,
        "supplemental_pair_ids": [],
    }

def test_job_pair_missing_pair_key_does_not_repeat_whole_batch(monkeypatch):
    """可解析响应缺少业务 ID 时，仅该配对降级为未评估，不再次调用 LLM。"""
    calls = 0
    first = _work_unit_pair()
    second = {
        **_work_unit_pair(),
        "pair_id": "PAIR_2",
        "work_unit": {"work_unit_id": "WUV_2", "project_id": "P_1", "source_refs": []},
    }

    def fake_call_json_llm(**kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        return {"pairs": [{
            "pair_key": "PAIR_1", "content_level": 4,
            "matched_element_ids": ["E_1"], "reason": "支持",
        }]}, {"status": "completed"}

    monkeypatch.setattr(job_capability_current, "call_json_llm", fake_call_json_llm)
    results, traces, degraded = _score_pair_batch(
        [first, second], "work_unit", {}, {}, "prompt"
    )

    assert calls == 1
    assert degraded is True
    assert results["PAIR_1"]["content_level"] == 4
    assert results["PAIR_2"]["assessment_status"] == "unassessed"
    assert traces[-1]["reason"] == "pair_result_partial"


def test_jd_batch_missing_unit_id_degrades_only_that_unit(monkeypatch):
    """JD 批量响应漏单元时，不重试整批；required 单元保守降级为可评分能力。"""
    calls = 0

    def fake_call_json_llm(**kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        return {"items": [{"job_unit_id": "JDU_1", "capabilities": []}]}, {"status": "completed"}

    monkeypatch.setattr(job_capability_current, "call_json_llm", fake_call_json_llm)
    result = extract_job_unit_capabilities_batch(
        "JOB_1",
        [
            {"job_unit_id": "JDU_1", "raw_text": "本科及以上", "scoring_role": "qualification"},
            {"job_unit_id": "JDU_2", "raw_text": "负责后端开发", "scoring_role": "required"},
        ],
    )

    assert calls == 1
    assert result["degraded"] is True
    assert result["items"] == [
        {"jobUnitId": "JDU_1", "capabilities": [], "degraded": False},
        {"jobUnitId": "JDU_2", "capabilities": [{"capability_key": "deterministic_primary", "capability_name": "负责后端开发", "capability_definition": "负责后端开发", "required_evidence_elements": [{"element_key": "source_requirement", "description": "负责后端开发"}], "role": "core", "quality_focus_ids": []}], "degraded": True},
    ]


def test_jd_batch_transport_schema_allows_business_layer_to_isolate_bad_item(monkeypatch):
    response = {
        "items": [
            {
                "job_unit_id": "JDU_OK",
                "capabilities": [{
                    "capability_key": "design",
                    "capability_name": "机械设计",
                    "capability_definition": "完成机械设备设计",
                    "required_evidence_elements": [{
                        "element_key": "design_output",
                        "description": "完成设计输出",
                    }],
                    "role": "core",
                    "quality_focus_ids": [],
                }],
            },
            {"job_unit_id": "JDU_BAD", "capabilities": ["invalid"]},
        ]
    }
    validate_json_schema(response, job_capability_current._batch_capability_schema())
    monkeypatch.setattr(
        job_capability_current,
        "call_json_llm",
        lambda **_kwargs: (response, {"status": "completed"}),
    )

    result = extract_job_unit_capabilities_batch(
        "JOB_1",
        [
            {"job_unit_id": "JDU_OK", "raw_text": "完成机械设备设计", "scoring_role": "required"},
            {"job_unit_id": "JDU_BAD", "raw_text": "负责有限元分析", "scoring_role": "required"},
        ],
    )

    assert result["items"][0]["capabilities"][0]["capability_key"] == "design"
    assert result["items"][0]["degraded"] is False
    assert result["items"][1]["capabilities"][0]["capability_key"] == "deterministic_primary"
    assert result["items"][1]["degraded"] is True


def test_jd_fallback_uses_frozen_classification_instead_of_text_keywords():
    """学历等词出现在可评分 JDUnit 中时，不能被 fallback 误删。"""
    result = job_capability_current._fallback_capabilities([
        {
            "job_unit_id": "JDU_REQUIRED",
            "raw_text": "负责机械设计，硕士学历背景优先",
            "requirement_type": "capability",
            "scoring_role": "required",
        },
        {
            "job_unit_id": "JDU_PREFERRED",
            "raw_text": "具备核电项目经验者优先",
            "requirement_type": "preferred_capability",
            "scoring_role": "preferred",
        },
        {
            "job_unit_id": "JDU_QUALIFICATION",
            "raw_text": "硕士及以上学历",
            "requirement_type": "hard_qualification",
            "scoring_role": "qualification",
        },
    ])

    assert result["JDU_REQUIRED"][0]["role"] == "core"
    assert result["JDU_REQUIRED"][0]["capability_definition"] == "负责机械设计，硕士学历背景优先"
    assert result["JDU_PREFERRED"][0]["role"] == "supporting"
    assert result["JDU_QUALIFICATION"] == []


def test_jd_batch_forces_provider_compatible_json_object_mode(monkeypatch):
    """岗位能力调用不得重新启用供应商未实现的 json_schema response_format。"""
    captured = {}

    def fake_call_json_llm(**kwargs):
        captured.update(kwargs)
        return {"items": [{"job_unit_id": "JDU_1", "capabilities": []}]}, {}

    monkeypatch.setattr(job_capability_current, "call_json_llm", fake_call_json_llm)
    extract_job_unit_capabilities_batch(
        "JOB_1",
        [{"job_unit_id": "JDU_1", "raw_text": "负责后端开发", "scoring_role": "required"}],
    )

    workflow = captured["settings_overrides"]["workflows"]["job_capability"]
    assert workflow["json_mode"] is True
    assert workflow["strict_json_schema"] is False
