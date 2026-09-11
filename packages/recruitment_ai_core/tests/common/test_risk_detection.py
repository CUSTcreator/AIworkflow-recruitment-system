from __future__ import annotations

import pytest

from recruitment_ai_core.common.risk_detection import _normalize


FACTS = [
    {"evidence_id": "WUV_1", "target_refs": ["WU_1", "P_1"]},
    {"evidence_id": "WUV_2", "target_refs": ["WU_2", "P_1"]},
]


def test_initial_risk_requires_two_real_conflicting_sources():
    risks = _normalize("APP_1", [{
        "risk_type": "ownership_conflict",
        "summary": "同一项目中的个人责任表述互相冲突",
        "source_evidence_refs": ["WUV_1", "WUV_2"],
        "target_refs": ["WU_1", "WU_2"],
        "verification_need": "核验实际负责范围",
    }], FACTS)

    assert len(risks) == 1
    assert risks[0]["status"] == "open"


def test_initial_risk_rejects_single_source_suspicion():
    with pytest.raises(ValueError, match="initial_risk_sources_invalid"):
        _normalize("APP_1", [{
            "risk_type": "authenticity_conflict",
            "summary": "表述不够详细",
            "source_evidence_refs": ["WUV_1"],
            "target_refs": ["WU_1"],
            "verification_need": "继续询问",
        }], FACTS)
