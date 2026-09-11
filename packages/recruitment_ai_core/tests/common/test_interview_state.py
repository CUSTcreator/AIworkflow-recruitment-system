from __future__ import annotations

from recruitment_ai_core.common.interview_state import update_interview_state


def test_disabled_llm_keeps_risk_open_and_creates_missing_targets() -> None:
    profile = {
        "risks": [{
            "risk_id": "R1", "risk_type": "ownership_conflict", "status": "open", "summary": "责任边界冲突",
            "target_refs": ["JC1"], "verification_need": "说明个人负责内容",
        }],
        "interview_targets": [],
    }
    risks, targets, risk_changes, target_changes, gap_changes = update_interview_state(
        profile=profile,
        parse_result={"experience_updates": [], "skill_claim_corrections": [], "interview_observations": []},
        stage="after_first_interview",
        existing_risks=profile["risks"],
        existing_targets=profile["interview_targets"],
        llm_config={"enabled": False},
    )

    assert risks[0]["status"] == "open"
    assert risk_changes == []
    assert gap_changes == []
    assert targets == []
    assert target_changes == []


def test_resolved_target_does_not_force_risk_resolution(monkeypatch) -> None:
    class Settings:
        fail_open = True

        @staticmethod
        def is_enabled_for(_workflow: str) -> bool:
            return True

    monkeypatch.setattr(
        "recruitment_ai_core.common.interview_state.load_llm_settings",
        lambda _config: Settings(),
    )
    monkeypatch.setattr(
        "recruitment_ai_core.common.interview_state.call_json_llm",
        lambda **_kwargs: ({
            "risk_results": [{"risk_id": "R1", "resolved": False, "reason": "没有明确解释原冲突"}],
            "target_results": [{"interview_target_id": "T1", "resolved": True, "reason": "已获得所需事实", "remaining_need": None}],
            "new_risks": [],
        }, {"status": "success"}),
    )
    profile = {
        "risks": [{"risk_id": "R1", "risk_type": "fact_conflict", "status": "open", "summary": "事实冲突"}],
        "interview_targets": [{"interview_target_id": "T1", "status": "open", "purpose": "resolve_risk", "target_type": "job_capability", "target_ids": ["JC1"], "risk_ids": ["R1"], "source_evidence_ids": []}],
    }
    risks, targets, risk_changes, target_changes, gap_changes = update_interview_state(
        profile=profile,
        parse_result={"experience_updates": [], "skill_claim_corrections": [], "interview_observations": []},
        stage="after_first_interview",
        existing_risks=profile["risks"],
        existing_targets=profile["interview_targets"],
        llm_config={"enabled": True},
    )

    assert risks[0]["status"] == "open"
    assert risk_changes == []
    assert targets[0]["status"] == "resolved"
    assert target_changes[0]["new_status"] == "resolved"
    assert gap_changes == []


def test_same_round_parse_conflict_becomes_review_risk_without_llm() -> None:
    profile = {"risks": [], "interview_targets": []}
    parse_result = {
        "interview_round_id": "ROUND_1",
        "experience_updates": [],
        "skill_claim_corrections": [],
        "interview_observations": [],
        "parse_conflicts": [{
            "conflict_type": "experience_updates",
            "target_id": "WU_1",
            "source_refs": [
                {"segment_id": "SEG_1", "quote": "候选人独立完成"},
                {"segment_id": "SEG_2", "quote": "候选人仅参与测试"},
            ],
        }],
    }

    risks, targets, risk_changes, _, _ = update_interview_state(
        profile=profile,
        parse_result=parse_result,
        stage="after_first_interview",
        llm_config={"enabled": False},
    )

    assert len(risks) == 1
    assert risks[0]["risk_type"] == "fact_conflict"
    assert risks[0]["target_refs"] == ["WU_1"]
    assert risk_changes[0]["change_type"] == "added"
    assert targets == []
