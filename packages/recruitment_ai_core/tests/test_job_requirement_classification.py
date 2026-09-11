from __future__ import annotations

import pytest

from recruitment_ai_core.job_capability import requirement_classification as module
from recruitment_ai_core.llm.errors import LLMResponseError


def _job(**overrides):
    value = {
        "title": "机械设计工程师",
        "education_requirement": "本科及以上",
        "major_requirement": "机械工程相关专业",
        "qualifications": [
            "3年以上相关工作经验；熟悉 Python 开发",
            "CET-6，必须通过",
            "证书优先",
            "身体健康、品行端正",
        ],
    }
    value.update(overrides)
    return value


def test_deterministic_rules_cover_explicit_hard_conditions_and_dedupe_degree():
    result = module.classify_job_requirements(_job(), llm_config={"enabled": False})

    hard = result["hardScreeningRules"]
    assert {(item["criterion_type"], item["expected_value"]) for item in hard} >= {
        ("minimum_degree", "本科"),
        ("minimum_experience_years", 3.0),
        ("certification", "CET-6"),
    }
    assert sum(item["criterion_type"] == "minimum_degree" for item in hard) == 1
    preferred = next(item for item in result["items"] if item["source_quote"] == "证书优先")
    assert preferred["category"] == "preferred_capability"
    assert not any(item["description"] == "证书优先" for item in hard)
    assert any(item["category"] == "non_scoring" for item in result["items"])


def test_certificate_rule_does_not_include_unrelated_text_from_same_clause():
    result = module.classify_job_requirements(
        _job(
            education_requirement="",
            qualifications=["通过大学英语六级CET-6考试、中文写作能力良好。"],
        ),
        llm_config={"enabled": False},
    )

    assert result["hardScreeningRules"][0]["expected_value"] == "CET-6"
    assert "中文写作" in result["hardScreeningRules"][0]["description"]


def test_model_result_is_source_grounded_and_only_pending_items_are_sent(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        pending = kwargs["messages"][1]["content"]
        assert "只分类输入中的岗位任职要求" in kwargs["messages"][0]["content"]
        assert "模糊要求" in pending
        return {
            "items": [{
                "source_id": "qualification:1:1",
                "source_quote": "模糊要求",
                "category": "required_capability",
                "criterion_code": None,
                "expected_value": None,
            }]
        }, {"provider": "test"}

    monkeypatch.setattr(module, "call_json_llm", fake_call)
    result = module.classify_job_requirements(
        _job(
            education_requirement="",
            qualifications=["模糊要求"],
        )
    )

    assert result["items"][0]["source_quote"] == "模糊要求"
    assert captured["json_schema"]["additionalProperties"] is False


def test_model_quote_mismatch_is_rejected_for_activity_fallback(monkeypatch):
    def fake_call(**_kwargs):
        return {
            "items": [{
                "source_id": "qualification:1:1",
                "source_quote": "改写后的要求",
                "category": "required_capability",
                "criterion_code": None,
                "expected_value": None,
            }]
        }, {}

    monkeypatch.setattr(module, "call_json_llm", fake_call)
    with pytest.raises(LLMResponseError, match="quote_mismatch"):
        module.classify_job_requirements(
            _job(education_requirement="", qualifications=["模糊要求"])
        )


def test_batch_classification_calls_llm_once_and_keeps_jobs_isolated(monkeypatch):
    calls = 0

    def fake_call(**kwargs):
        nonlocal calls
        calls += 1
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        return {
            "jobs": [
                {
                    "job_key": job["job_key"],
                    "items": [
                        {
                            "source_id": item["source_id"],
                            "source_quote": item["source_text"],
                            "category": "required_capability",
                            "criterion_code": None,
                            "expected_value": None,
                        }
                        for item in job["requirements"]
                    ],
                }
                for job in request["jobs"]
            ]
        }, {"provider": "test"}

    monkeypatch.setattr(module, "call_json_llm", fake_call)
    result = module.classify_job_requirements_batch([
        {"job_key": "row-1", "title": "岗位一", "education_requirement": "本科及以上", "qualifications": ["服从现场安排"]},
        {"job_key": "row-2", "title": "岗位二", "education_requirement": "", "qualifications": ["适应长期出差"]},
    ])

    assert calls == 1
    assert [item["job_key"] for item in result["jobs"]] == ["row-1", "row-2"]
    assert result["jobs"][0]["hardScreeningRules"][0]["criterion_type"] == "minimum_degree"
    assert result["jobs"][1]["hardScreeningRules"] == []


def test_batch_classification_degrades_only_the_malformed_job(monkeypatch):
    def fake_call(**kwargs):
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        first, second = request["jobs"]
        return {
            "jobs": [
                {
                    "job_key": first["job_key"],
                    "items": [{
                        "source_id": first["requirements"][0]["source_id"],
                        "source_quote": first["requirements"][0]["source_text"],
                        "category": "required_capability",
                        "criterion_code": None,
                        "expected_value": None,
                    }],
                },
                {
                    "job_key": second["job_key"],
                    "items": [{
                        "source_id": second["requirements"][0]["source_id"],
                        "source_quote": "模型改写了原文",
                        "category": "hard_screen",
                        "criterion_code": "custom",
                        "expected_value": "错误条件",
                    }],
                },
            ]
        }, {}

    monkeypatch.setattr(module, "call_json_llm", fake_call)
    result = module.classify_job_requirements_batch([
        {"job_key": "row-1", "title": "岗位一", "education_requirement": "", "qualifications": ["服从现场安排"]},
        {"job_key": "row-2", "title": "岗位二", "education_requirement": "", "qualifications": ["适应长期出差"]},
    ])

    by_key = {item["job_key"]: item for item in result["jobs"]}
    assert by_key["row-1"]["degraded"] is False
    assert by_key["row-2"]["degraded"] is True
    assert by_key["row-2"]["hardScreeningRules"] == []
    assert result["degraded"] is True


def test_batch_classification_rejects_extra_fields_per_job(monkeypatch):
    def fake_call(**kwargs):
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        first, second = request["jobs"]
        jobs = []
        for index, job in enumerate((first, second)):
            source = job["requirements"][0]
            item = {
                "source_id": source["source_id"],
                "source_quote": source["source_text"],
                "category": "required_capability",
                "criterion_code": None,
                "expected_value": None,
            }
            if index == 1:
                item["explanation"] = "不允许返回的多余字段"
            jobs.append({"job_key": job["job_key"], "items": [item]})
        return {"jobs": jobs}, {}

    monkeypatch.setattr(module, "call_json_llm", fake_call)
    result = module.classify_job_requirements_batch([
        {"job_key": "row-1", "title": "岗位一", "qualifications": ["服从现场安排"]},
        {"job_key": "row-2", "title": "岗位二", "qualifications": ["适应长期出差"]},
    ])

    by_key = {item["job_key"]: item for item in result["jobs"]}
    assert by_key["row-1"]["degraded"] is False
    assert by_key["row-2"]["degraded"] is True
    assert by_key["row-2"]["items"][0]["category"] == "non_scoring"


def test_deterministic_fallback_is_usable_and_does_not_invent_hard_rules():
    result = module.deterministic_requirement_fallback(
        _job(education_requirement="", qualifications=["候选人最好有相关背景"])
    )

    assert result["degraded"] is True
    assert result["hardScreeningRules"] == []
    assert result["items"][0]["category"] == "non_scoring"


@pytest.mark.parametrize(
    ("qualification", "expected_category"),
    [
        ("仪器科学与技术、控制科学与工程、控制工程及其相关专业", "non_scoring"),
        ("核科学与技术、能源动力及其相关专业", "non_scoring"),
        ("机械工程、力学及相关专业", "non_scoring"),
        ("专业要求：计算机科学与技术", "non_scoring"),
        ("相关专业背景优先", "non_scoring"),
        ("熟悉机械专业设计软件", "required_capability"),
        ("掌握核工程专业技术知识", "required_capability"),
    ],
)
def test_major_clause_detection_does_not_confuse_major_names_with_capabilities(
    qualification: str,
    expected_category: str,
):
    result = module.deterministic_requirement_fallback(
        _job(education_requirement="", qualifications=[qualification])
    )

    assert result["items"][0]["category"] == expected_category


def test_batch_classification_preserves_extracted_major_provenance(monkeypatch):
    def unexpected_call(**_kwargs):
        raise AssertionError("已提取的专业条款不应再次发送给 LLM")

    monkeypatch.setattr(module, "call_json_llm", unexpected_call)
    result = module.classify_job_requirements_batch([{
        "job_key": "row-1",
        "title": "控制工程师",
        "education_requirement": "",
        "major_requirement": "仪器科学与技术",
        "major_requirement_source_quote": "仪器科学与技术；",
        "qualifications": ["仪器科学与技术"],
    }])

    assert result["jobs"][0]["items"][0]["category"] == "non_scoring"


@pytest.mark.parametrize(
    ("education", "expected"),
    [
        ("本科优先", None),
        ("本科及以上，硕士优先", "本科"),
        ("硕士及以上", "硕士"),
    ],
)
def test_degree_rules_distinguish_minimum_from_preference(education: str, expected: str | None):
    result = module.classify_job_requirements(
        _job(education_requirement=education, qualifications=[]),
        llm_config={"enabled": False},
    )
    degrees = [
        item["expected_value"]
        for item in result["hardScreeningRules"]
        if item["criterion_type"] == "minimum_degree"
    ]
    assert degrees == ([] if expected is None else [expected])
