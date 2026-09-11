from __future__ import annotations

import json

import pytest

from recruitment_ai_core.candidate_routing.major_routing import (
    JobMajorRequirement,
    MajorRoutingError,
    route_candidate_major,
)


def test_routes_all_constrained_jobs_in_one_model_response(monkeypatch):
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return {"results": [
            {"job_id": "JOB_1", "relation": "exact", "basis": "计算机专业符合岗位要求"},
            {"job_id": "JOB_2", "relation": "unrelated", "basis": "计算机专业与土木工程不相关"},
        ]}, {"status": "success"}

    monkeypatch.setattr("recruitment_ai_core.candidate_routing.major_routing.call_json_llm", fake_call)
    result, _ = route_candidate_major(
        candidate_major="计算机科学与技术", source_quote="计算机科学与技术",
        jobs=[
            JobMajorRequirement("JOB_1", "JDV_1", "软件开发", "计算机相关专业"),
            JobMajorRequirement("JOB_2", "JDV_2", "土木设计", "土木工程专业"),
        ],
    )
    assert len(calls) == 1
    request = json.loads(calls[0]["messages"][1]["content"])
    assert request["candidate_major"] == "计算机科学与技术"
    assert request["source_quote"] == "计算机科学与技术"
    assert [item["job_id"] for item in request["jobs"]] == ["JOB_1", "JOB_2"]
    assert all("title" not in item for item in request["jobs"])
    prompt = calls[0]["messages"][0]["content"]
    assert "不得使用岗位名称" in prompt
    assert "不得遗漏、重复或新增岗位" in prompt
    assert result == [
        {"job_id": "JOB_1", "matched": True, "reason": "专业匹配：计算机专业符合岗位要求"},
        {"job_id": "JOB_2", "matched": False, "reason": "专业不匹配：计算机专业与土木工程不相关"},
    ]


def test_related_relation_deterministically_derives_matched_true(monkeypatch):
    monkeypatch.setattr(
        "recruitment_ai_core.candidate_routing.major_routing.call_json_llm",
        lambda **_: ({
            "results": [{
                "job_id": "JOB_MECHANICS",
                "relation": "related",
                "basis": "流体力学属于力学分支，岗位接受力学相关专业",
            }],
        }, {"status": "success"}),
    )

    decisions, _ = route_candidate_major(
        candidate_major="流体力学",
        source_quote="专业：流体力学",
        jobs=[
            JobMajorRequirement(
                "JOB_MECHANICS", "JDV_1", "力学工程师", "力学及相关专业"
            )
        ],
    )

    assert decisions == [{
        "job_id": "JOB_MECHANICS",
        "matched": True,
        "reason": "专业匹配：流体力学属于力学分支，岗位接受力学相关专业",
    }]


def test_rejects_incomplete_model_decisions(monkeypatch):
    monkeypatch.setattr(
        "recruitment_ai_core.candidate_routing.major_routing.call_json_llm",
        lambda **_: ({"results": []}, {}),
    )
    with pytest.raises(MajorRoutingError, match="result_invalid"):
        route_candidate_major(
            candidate_major="计算机科学与技术", source_quote="计算机科学与技术",
            jobs=[JobMajorRequirement("JOB_1", "JDV_1", "软件开发", "计算机相关专业")],
        )


def test_job_without_major_requirement_does_not_call_model():
    result, trace = route_candidate_major(
        candidate_major="计算机科学与技术", source_quote="计算机科学与技术",
        jobs=[JobMajorRequirement("JOB_1", "JDV_1", "通用岗位", "")],
    )
    assert result[0]["matched"] is True
    assert trace["mode"] == "no_constrained_jobs"


def test_missing_major_keeps_unrestricted_jobs_routable() -> None:
    decisions, trace = route_candidate_major(
        candidate_major="",
        source_quote="",
        jobs=[
            JobMajorRequirement("JOB_OPEN", "JDV_OPEN", "通用岗位", ""),
            JobMajorRequirement("JOB_LIMITED", "JDV_LIMITED", "专业岗位", "机械工程"),
        ],
    )

    assert decisions == [
        {
            "job_id": "JOB_LIMITED",
            "matched": False,
            "reason": "候选人专业信息不足，需人工选择岗位",
            "requires_manual_selection": True,
        },
        {"job_id": "JOB_OPEN", "matched": True, "reason": "岗位未设置专业要求"},
    ]
    assert trace["mode"] == "candidate_major_missing"


def test_unavailable_model_keeps_every_job_in_the_routing_matrix(monkeypatch) -> None:
    monkeypatch.setattr(
        "recruitment_ai_core.candidate_routing.major_routing.call_json_llm",
        lambda **_: (None, {"mode": "disabled"}),
    )

    decisions, trace = route_candidate_major(
        candidate_major="计算机科学与技术",
        source_quote="计算机科学与技术",
        jobs=[
            JobMajorRequirement("JOB_OPEN", "JDV_1", "通用岗位", ""),
            JobMajorRequirement("JOB_LIMITED", "JDV_2", "后端开发", "计算机相关专业"),
        ],
    )

    assert decisions == [
        {"job_id": "JOB_LIMITED", "matched": False, "reason": "自动专业匹配暂不可用，需人工选择岗位", "requires_manual_selection": True},
        {"job_id": "JOB_OPEN", "matched": True, "reason": "岗位未设置专业要求"},
    ]
    assert trace["mode"] == "candidate_major_routing_llm_unavailable"
