from recruitment_ai_core.resume_structuring.skill_claim_extractor import (
    _validate,
    build_degraded_skill_statements,
)
from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir


STATEMENTS = [{
    "skill_statement_id": "SS_1",
    "source_ref": {
        "section": "专业技能",
        "quote": "熟悉 Spring Boot、JVM 与 ConcurrentHashMap",
    },
}]


def test_skill_details_must_come_from_statement_source() -> None:
    valid, error = _validate({
        "statements": [{
            "statement_id": "SS_1",
            "claims": [{
                "skill_name": "Java生态",
                "details": ["SpringBoot", "JVM", "ConcurrentHashMap"],
            }],
        }],
    }, STATEMENTS)
    assert valid is True
    assert error == ""


def test_inferred_skill_detail_is_rejected() -> None:
    valid, error = _validate({
        "statements": [{
            "statement_id": "SS_1",
            "claims": [{
                "skill_name": "Java生态",
                "details": ["高并发调优"],
            }],
        }],
    }, STATEMENTS)
    assert valid is False
    assert error == "claim_detail_not_in_source"


def test_explicit_skill_aliases_are_routed_to_skill_statements() -> None:
    resume_ir = build_resume_ir(
        "SKILL_ALIASES",
        "软件能力\n熟悉 SolidWorks、AutoCAD\n工具：Git、Docker",
    )

    skill_texts = [
        span.text for span in resume_ir.candidate_spans if span.section == "skills"
    ]
    assert "熟悉 SolidWorks、AutoCAD" in skill_texts
    assert "工具：Git、Docker" in skill_texts


def test_profile_technical_assertions_are_candidates_without_product_dictionary() -> None:
    resume_ir = build_resume_ir(
        "PROFILE_SKILL",
        "个人信息\n个人特长/自我评价：责任心强；能熟练运用多项仿真软件（OpenFOAM、Ansys）；具有团队合作精神",
    )

    degraded = build_degraded_skill_statements(resume_ir)

    assert len(degraded) == 1
    claim = degraded[0]["skill_claims"][0]
    assert claim["skill_name"] == "专业技能"
    assert claim["details"] == ["能熟练运用多项仿真软件（OpenFOAM、Ansys）"]
    assert claim["source_refs"][0]["quote"].startswith("个人特长/自我评价")


def test_explicit_skill_text_has_source_backed_degraded_result() -> None:
    resume_ir = build_resume_ir(
        "EXPLICIT_SKILL_DEGRADE",
        "专业技能\n数据库：熟悉 MySQL、Oracle，具备 SQL 优化能力",
    )

    degraded = build_degraded_skill_statements(resume_ir)

    assert degraded[0]["skill_claims"][0]["skill_name"] == "数据库"
    assert degraded[0]["skill_claims"][0]["details"] == [
        "熟悉 MySQL、Oracle，具备 SQL 优化能力"
    ]
