from __future__ import annotations

import json

import pytest

from recruitment_ai_core.llm.errors import LLMResponseError
from recruitment_ai_core.resume_structuring import (
    resume_ir_from_structure,
    structure_resume,
)
from recruitment_ai_core.resume_structuring.work_unit_extractor import (
    _extract_project,
    _project_input,
    _schema,
    build_degraded_project_work_units,
    extract_work_units,
)
from recruitment_ai_core.resume_structuring.assembler import assemble_repaired_ir
from recruitment_ai_core.resume_structuring.activity_pipeline import prepare_structure
from recruitment_ai_core.resume_structuring.llm_repair import repair_resume_structure
from recruitment_ai_core.resume_structuring.validator import (
    StructureValidation,
    validate_resume_structure,
)
from recruitment_ai_core.resume_structuring.pipeline import (
    _layout_project_title_hints,
    _normalize_blocks,
)
from recruitment_ai_core.screening_scoring.contracts import ScorableWorkUnit, SourceBullet
from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir
from recruitment_ai_core.screening_scoring.section_resolver import resolve_section_anchor


@pytest.fixture(autouse=True)
def _stub_skill_claim_extraction(monkeypatch):
    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_skill_claims",
        lambda resume_ir, **kwargs: ([], [{"stage": "skill_claim"}], None),
    )


def _blocks(text: str) -> list[dict]:
    return [
        {
            "block_id": f"B_{index:03d}",
            "page": 1,
            "order": index,
            "text": line,
            "block_type": "text",
            "bbox": [10, index * 20, 500, index * 20 + 16],
            "font_size": 11,
            "is_bold": index in {1, 2, 4},
            "heading_level": None,
            "source_parser": "test",
        }
        for index, line in enumerate(text.splitlines(), start=1)
    ]


def test_clean_block_structure_only_calls_llm_for_work_units(
    monkeypatch,
) -> None:
    text = "\n".join(
        [
            "项目经历",
            "智能问答系统 2024.03-2024.08",
            "负责构建混合检索链路，Top-5召回率提升18%",
            "专业技能",
            "Python、FastAPI、LangChain",
        ]
    )
    def fake_extract(resume_ir, **kwargs):
        del kwargs
        return resume_ir.scorable_work_units, [{"stage": "work_unit"}], None

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        fake_extract,
    )
    result = structure_resume(
        candidate_id="CLEAN",
        resume_text=text,
        document_blocks=_blocks(text),
        llm_config={"enabled": True},
    )
    assert result["status"] == "passed"
    assert result["method"] == "deterministic_outline+llm_work_units"
    assert result["repair_trace"] == [
        {"stage": "work_unit"},
        {"stage": "skill_claim"},
    ]
    resume_ir = resume_ir_from_structure(result)
    assert len(resume_ir.experience_units) == 1
    assert resume_ir.candidate_spans[0].source_block_ids == ["B_001"]


def test_other_separator_lines_do_not_trigger_section_reentry(
    monkeypatch,
) -> None:
    text = "\n".join(
        [
            "项目经历",
            "智能问答系统 2024.03-2024.08",
            "负责构建混合检索链路",
            "-",
            "完成失败样本分析和回归测试",
            "---",
            "专业技能",
            "Python、FastAPI、LangChain",
        ]
    )

    def fake_extract(resume_ir, **kwargs):
        del kwargs
        return resume_ir.scorable_work_units, [], None

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        fake_extract,
    )
    result = structure_resume(
        candidate_id="SEPARATOR",
        resume_text=text,
        document_blocks=_blocks(text),
        llm_config={"enabled": False},
    )
    assert result["status"] == "passed"
    assert "suspicious_section_order" not in result["validation"]["warnings"]


def test_multiline_parser_block_is_split_before_structuring(
    monkeypatch,
) -> None:
    text = "\n".join(
        [
            "教育经历",
            "示例大学 本科",
            "项目经历",
            "智能问答系统 2024.03-2024.08",
            "负责构建混合检索链路",
            "完成失败样本分析和回归测试",
            "专业技能",
            "Python、FastAPI、LangChain",
        ]
    )
    block = {
        "block_id": "B_0001",
        "page": 1,
        "order": 1,
        "text": text,
        "block_type": "text",
        "source_parser": "local_pymupdf",
    }

    def fake_extract(resume_ir, **kwargs):
        del kwargs
        return resume_ir.scorable_work_units, [], None

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        fake_extract,
    )
    result = structure_resume(
        candidate_id="MULTILINE",
        resume_text=text,
        document_blocks=[block],
        llm_config={"enabled": False},
    )
    assert result["status"] == "passed"
    resume_ir = resume_ir_from_structure(result)
    assert len(resume_ir.experience_units) == 1
    assert resume_ir.candidate_spans[0].source_block_ids == ["B_0001"]


def test_multiline_parser_block_rejoins_pdf_soft_wraps() -> None:
    block = {
        "block_id": "B_PAGE_1",
        "page": 1,
        "order": 1,
        "text": (
            "项目经历\n"
            "智能问答系统 2025.01-2025.06\n"
            "基于LangGraph实现问题校验、查询改写、片段打分与补充检索；离线评测中幻觉率由17.4\n"
            "%降至0%。\n"
            "- 新建独立工作项"
        ),
        "block_type": "text",
        "source_parser": "local_pymupdf",
    }
    normalized = _normalize_blocks([block], "")
    texts = [item["text"] for item in normalized]
    assert any("17.4%降至0%" in text for text in texts)
    assert "- 新建独立工作项" in texts
    merged = next(item for item in normalized if "17.4%降至0%" in item["text"])
    assert merged["source_fragment_ids"] == ["B_PAGE_1_L003", "B_PAGE_1_L004"]


def test_non_experience_title_candidates_do_not_create_empty_project_error(
    monkeypatch,
) -> None:
    text = "\n".join(
        [
            "candidate@example.com",
            "1992",
            "教育经历",
            "示例大学 本科",
            "项目经历",
            "智能问答系统 2025.01-2025.06",
            "负责构建混合检索链路并完成失败样本分析。",
            "专业技能",
            "Python、FastAPI、LangGraph",
        ]
    )

    def fake_extract(resume_ir, **kwargs):
        del kwargs
        return resume_ir.scorable_work_units, [], None

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        fake_extract,
    )
    result = structure_resume(
        candidate_id="CONTACT_HEADER",
        resume_text=text,
        document_blocks=_blocks(text),
        llm_config={"enabled": False},
    )
    assert result["status"] == "passed"
    assert not any(
        item["code"] == "project_without_content"
        for item in result["validation"]["errors"]
    )


def test_multiple_complete_projects_do_not_trigger_false_empty_project_error(
    monkeypatch,
) -> None:
    """两个项目均有简介、技术栈和要点时，不应触发无内容项目误判。"""
    text = "\n".join(
        [
            "项目经历",
            "KnowFlow Agentic RAG 平台 2025.12-2026.04",
            "项目简介：实现多路检索、工具调用和会话记忆。",
            "技术栈：Spring Boot、PostgreSQL、Redis",
            "参与文档入库链路开发，并完成异构文档并发解析。",
            "构建链路治理能力并补充失败恢复机制。",
            "RepoPilot 代码智能体 2026.02-2026.04",
            "项目简介：实现工具调用和受控代码修改。",
            "技术栈：Python、MCP、AsyncIO",
            "构建状态驱动的多轮执行链路，并补充异常恢复能力。",
            "实现受控代码修改并记录完整执行追踪。",
        ]
    )

    def fake_extract(resume_ir, **kwargs):
        del kwargs
        return resume_ir.scorable_work_units, [], None

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        fake_extract,
    )
    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.repair_resume_structure",
        lambda **_: pytest.fail("完整多项目简历不应触发 LLM 大纲修复"),
    )

    result = structure_resume(
        candidate_id="MULTI_PROJECT",
        resume_text=text,
        document_blocks=_blocks(text),
        llm_config={"enabled": False},
    )

    assert result["status"] == "passed"
    assert not any(
        item["code"] == "project_without_content"
        for item in result["validation"]["errors"]
    )
    resume_ir = resume_ir_from_structure(result)
    assert len(resume_ir.experience_units) == 2
    assert all(item.source_bullet_ids for item in resume_ir.experience_units)

def test_standard_form_fields_and_following_sections_do_not_split_projects(
    monkeypatch,
) -> None:
    """标准招聘表单的字段行、荣誉和家庭信息不能被拆成伪项目。"""
    text = "\n".join(
        [
            "教育经历",
            "示例大学 硕士",
            "实习经历",
            "智能制造检测系统 2024-05～2024-11 (6个月)",
            "岗位 研发实习生",
            "证明人 张老师",
            "项目描述 基于机器学习的设备缺陷检测系统",
            "项目中职责 负责完成数据清洗、模型训练和验证。",
            "获奖情况",
            "获奖时间 2024-06-01 获奖项 优秀学生",
            "论文/专著",
            "作者顺序 第一作者 所属期刊 工程技术",
            "家庭情况",
            "与本人关系 父亲 工作单位 示例单位 职务 工程师",
            "附加信息",
            "本人承诺上述信息真实有效",
            "简历附件",
            "上传证明材料 毕业证.pdf",
        ]
    )

    def fake_extract(resume_ir, **kwargs):
        del kwargs
        return resume_ir.scorable_work_units, [], None

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        fake_extract,
    )
    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.repair_resume_structure",
        lambda **_: pytest.fail("标准表单不应触发 LLM 大纲修复"),
    )

    result = structure_resume(
        candidate_id="STANDARD_FORM",
        resume_text=text,
        document_blocks=_blocks(text),
        llm_config={"enabled": False},
    )

    assert result["status"] == "passed"
    assert result["validation"]["errors"] == []
    resume_ir = resume_ir_from_structure(result)
    title_texts = {
        span.text
        for span in resume_ir.candidate_spans
        if span.rough_type == "experience_title"
    }
    assert "岗位 研发实习生" not in title_texts
    assert "证明人 张老师" not in title_texts
    assert "获奖情况" not in title_texts
    assert "家庭情况" not in title_texts
    bullet_texts = {item.raw_text for item in resume_ir.source_bullets}
    assert "岗位 研发实习生" not in bullet_texts
    assert "证明人 张老师" not in bullet_texts
    assert "项目描述 基于机器学习的设备缺陷检测系统" not in bullet_texts
    assert any(
        text.startswith("项目中职责：负责完成数据清洗、模型训练和验证")
        for text in bullet_texts
    )


def test_standard_form_project_name_values_create_project_boundaries() -> None:
    text = "\n".join([
        "项目经历",
        "项目名称：智能制造检测系统",
        "项目中职责：负责模型训练与验证",
        "项目名称：设备采购管理平台",
        "项目中职责：负责供应商评估和采购执行",
    ])

    resume_ir = build_resume_ir("STANDARD_PROJECT_NAMES", text)

    assert [item.title for item in resume_ir.experience_units] == [
        "智能制造检测系统",
        "设备采购管理平台",
    ]
    assert [len(item.source_bullet_ids) for item in resume_ir.experience_units] == [1, 1]


def test_date_table_fragment_does_not_open_new_experience_unit() -> None:
    resume_ir = build_resume_ir(
        "DATE_TABLE_FRAGMENT",
        "\n".join([
            "项目经历",
            "智能制造检测系统 2024.01-2024.04",
            "负责完成模型训练",
            "| -- | 2024-05 ~ 至今 |",
            "完成部署并上线",
        ]),
    )

    assert [item.title for item in resume_ir.experience_units] == [
        "智能制造检测系统 2024.01-2024.04"
    ]
    assert "| -- | 2024-05 ~ 至今 |" not in {
        item.raw_text for item in resume_ir.source_bullets
    }


def test_placeholder_date_rows_followed_by_descriptions_create_boundaries(
    monkeypatch,
) -> None:
    """表格式项目没有名称时，日期行仍应按项目描述切开经历单元。"""
    text = "\n".join([
        "项目经历",
        "-- | -- | 2024-05 ~ 至今",
        "项目描述 本项目为胎膜吊装与夹紧系统的研发",
        "项目职责 负责系统设计和验证",
        "完成方案评审与现场测试",
        "-- | -- | 2024-05 ~ 至今",
        "项目描述 基于机器学习的机加工过程检测研究项目介绍：研究刀具磨损项目职责：负责模型训练",
        "项目职责 负责模型训练和验证",
        "完成模型效果分析和报告撰写",
    ])

    def fake_extract(resume_ir, **kwargs):
        del kwargs
        return resume_ir.scorable_work_units, [], None

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        fake_extract,
    )
    result = structure_resume(
        candidate_id="TABLE_PROJECTS",
        resume_text=text,
        document_blocks=_blocks(text),
        llm_config={"enabled": False},
    )

    resume_ir = resume_ir_from_structure(result)
    assert [item.title for item in resume_ir.experience_units] == [
        "本项目为胎膜吊装与夹紧系统的研发",
        "基于机器学习的机加工过程检测研究",
    ]
    # 内嵌在项目描述后的明确“项目职责”也必须成为独立评分证据。
    assert [len(item.source_bullet_ids) for item in resume_ir.experience_units] == [2, 3]


def test_placeholder_date_rows_without_table_separators_create_boundaries(
    monkeypatch,
) -> None:
    """MinerU may preserve placeholder date rows but drop every table pipe."""
    text = "\n".join([
        "项目经历",
        "-- -- 2024-05～至今 (1年11个月)",
        "项目描述 胎膜吊装与夹紧系统的研发",
        "项目职责 负责系统设计和验证",
        "-- -- 2024-05～至今 (1年11个月)",
        "项目描述 基于机器学习的机加工过程检测研究",
        "项目职责 负责模型训练和验证",
    ])

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.pipeline.extract_work_units",
        lambda resume_ir, **_kwargs: (resume_ir.scorable_work_units, [], None),
    )
    result = structure_resume(
        candidate_id="TABLE_PROJECTS_NO_PIPES",
        resume_text=text,
        document_blocks=_blocks(text),
        llm_config={"enabled": False},
    )

    resume_ir = resume_ir_from_structure(result)
    assert [item.title for item in resume_ir.experience_units] == [
        "胎膜吊装与夹紧系统的研发",
        "基于机器学习的机加工过程检测研究",
    ]


def test_redaction_keeps_at_metrics_but_removes_real_email() -> None:
    resume_ir = build_resume_ir(
        "METRIC_REDACTION",
        "\n".join([
            "candidate@example.com",
            "项目经历",
            "检索系统 2025.01-2025.06",
            "将检索排序质量 MRR@5 提升15%",
        ]),
    )

    assert "[email]" in resume_ir.candidate_spans[0].text
    assert any("MRR@5" in item.raw_text for item in resume_ir.source_bullets)


def test_placeholder_date_rows_split_without_bbox_metadata() -> None:
    resume_ir = build_resume_ir(
        "MARKDOWN_TABLE_PROJECTS",
        "\n".join([
            "项目经历",
            "-- | -- | 2024-05 ~ 至今",
            "项目描述 项目A",
            "项目职责 负责项目A实施",
            "-- | -- | 2024-06 ~ 至今",
            "项目描述 项目B",
            "项目职责 负责项目B实施",
        ]),
    )

    assert [item.title for item in resume_ir.experience_units] == ["项目A", "项目B"]


def test_company_rows_with_spaced_date_ranges_create_boundaries() -> None:
    resume_ir = build_resume_ir(
        "SPACED_COMPANY_DATES",
        "\n".join([
            "实习经历",
            "华建集团上海建筑设计研究院 | -- | 2018-01 ~ 2018-06",
            "实习内容 负责结构设计与计算复核",
            "上海建工一建集团徐汇梦中心项目 | -- | 2017-08 ~ 2017-09",
            "实习内容 负责施工技术支持与资料整理",
        ]),
    )

    assert [item.title for item in resume_ir.experience_units] == [
        "华建集团上海建筑设计研究院 | -- | 2018-01 ~ 2018-06",
        "上海建工一建集团徐汇梦中心项目 | -- | 2017-08 ~ 2017-09",
    ]


def test_layout_gap_hints_are_local_and_not_global() -> None:
    blocks = _blocks("\n".join([
        "项目经历",
        "智能问答系统 2024.03-2024.08",
        "负责构建检索链路",
        "设备采购管理平台",
        "负责供应商评估",
    ]))
    blocks[3]["bbox"] = [10, 100, 500, 116]
    assert 4 in _layout_project_title_hints(blocks)


def test_overmerged_project_descriptions_warn_without_blocking_source_evidence() -> None:
    text = "\n".join([
        "项目经历",
        "项目描述 项目A",
        "项目职责 负责项目A实施",
        "项目描述 项目B",
        "项目职责 负责项目B实施",
    ])
    blocks = [
        {"block_id": f"B_{index:03d}", "order": index, "text": line}
        for index, line in enumerate(text.splitlines(), start=1)
    ]
    resume_ir = build_resume_ir("OVERMERGED", text)

    validation = validate_resume_structure(resume_ir, blocks)

    assert validation.accepted is True
    assert validation.errors == []
    assert "experience_units_overmerged" in validation.warnings


@pytest.mark.parametrize(
    ("title", "error_code"),
    [
        ("2024.05-2024.08", "project_title_is_date_only"),
        ("| -- | 2024.05-2024.08 |", "project_title_is_table_fragment"),
        ("负责完成模型训练", "project_title_is_action_sentence"),
        ("召回率提升18%", "project_title_is_result_fragment"),
    ],
)
def test_llm_repair_rejects_objectively_invalid_project_title(
    title: str, error_code: str
) -> None:
    prepared = _repair_prepared(f"项目经历\n{title}\n补充项目内容")
    all_ids = [str(item["block_id"]) for item in prepared["blocks"]]
    payload = _repair_payload(
        prepared,
        projects=[{
            "title_block_ids": [all_ids[1]],
            "content_block_ids": [all_ids[2]],
        }],
    )

    with pytest.raises(ValueError, match=error_code):
        assemble_repaired_ir(
            candidate_id="REPAIR_INVALID_TITLE",
            resume_text=prepared["resumeText"],
            blocks=prepared["blocks"],
            payload=payload,
            deterministic_ir=resume_ir_from_structure({
                "status": "passed", "resume_ir": prepared["resumeIr"]
            }),
            repair_trace=[],
        )


def test_background_only_project_does_not_create_work_unit_activity_input() -> None:
    from dataclasses import asdict

    from recruitment_ai_core.resume_structuring.activity_pipeline import work_unit_inputs

    resume_ir = build_resume_ir(
        "BACKGROUND_ONLY",
        "项目经历\n项目名称：设备信息平台\n项目描述：用于展示设备台账",
    )

    assert resume_ir.experience_units
    assert resume_ir.source_bullets == []
    assert work_unit_inputs({"resumeIr": asdict(resume_ir)}) == []


def test_consecutive_project_titles_warn_without_forcing_outline_repair() -> None:
    text = "\n".join(
        [
            "项目经历",
            "项目A 2024.03-2024.05",
            "项目B 2024.06-2024.08",
            "负责实现检索链路并完成测试",
        ]
    )
    prepared = prepare_structure(
        candidate_id="REVIEW",
        resume_text=text,
        document_blocks=_blocks(text),
    )
    assert prepared["validation"]["accepted"] is True
    assert "project_without_content" in prepared["validation"]["warnings"]


def test_llm_repair_rejects_unknown_source_block(
) -> None:
    prepared = _repair_prepared(
        "项目经历\n项目A 2024.03-2024.05\n负责实现检索链路并完成测试"
    )
    known_ids = [str(item["block_id"]) for item in prepared["blocks"]]
    payload = {
        "sections": [{"section_type": "experience", "block_ids": [known_ids[0]]}],
        "projects": [{
            "title_block_ids": ["B_NOT_EXISTS"],
            "content_block_ids": [known_ids[2]],
        }],
        "unresolved_block_ids": [],
        "_repair_scope_block_ids": known_ids,
    }
    with pytest.raises(ValueError, match="unknown_block_ids"):
        assemble_repaired_ir(
            candidate_id="INVALID_ID",
            resume_text=prepared["resumeText"],
            blocks=prepared["blocks"],
            payload=payload,
            deterministic_ir=resume_ir_from_structure({
                "status": "passed", "resume_ir": prepared["resumeIr"]
            }),
            repair_trace=[],
        )


def test_section_regions_prevent_honors_and_attachments_from_becoming_skills() -> None:
    prepared = prepare_structure(
        candidate_id="SECTION_REGIONS",
        resume_text="\n".join([
            "专业技熊",  # one-character OCR error: 专业技能
            "熟悉 Python、FastAPI 和 PostgreSQL",
            "荣誉奖项",
            "研究生会优秀学生干部",
            "简历附件",
            "某大学本科成绩单.pdf",
        ]),
        document_blocks=None,
    )
    spans = prepared["resumeIr"]["candidate_spans"]
    assert next(item for item in spans if "Python" in item["text"])["section"] == "skills"
    assert next(item for item in spans if "研究生会" in item["text"])["section"] == "honors"
    assert next(item for item in spans if "成绩单" in item["text"])["section"] == "profile"


def test_resume_without_section_heading_keeps_legacy_experience_fallback() -> None:
    prepared = prepare_structure(
        candidate_id="NO_SECTION_HEADING",
        resume_text="智能检测系统 2024.01-2024.03\n负责模型训练并完成验证",
        document_blocks=None,
    )

    assert prepared["resumeIr"]["experience_units"]
    assert prepared["resumeIr"]["source_bullets"]


def test_section_prefix_in_prose_is_not_a_section_anchor() -> None:
    assert resolve_section_anchor("项目经历丰富，熟悉完整研发流程") is None


def test_narrative_project_reference_is_not_a_project_title() -> None:
    resume_ir = build_resume_ir(
        "NARRATIVE_TITLE",
        "项目经历\n项目A 2024.01-2024.03\n安全，目前正在撰写相关论文\n负责完成验证",
    )

    assert [item.title for item in resume_ir.experience_units] == [
        "项目A 2024.01-2024.03"
    ]


def test_role_only_project_field_is_context_not_source_bullet() -> None:
    resume_ir = build_resume_ir(
        "ROLE_ONLY",
        "项目经历\n智能检测系统\n项目中职责：开发人员\n负责完成模型训练与验证",
    )

    assert [item.raw_text for item in resume_ir.source_bullets] == [
        "负责完成模型训练与验证"
    ]


def _repair_prepared(text: str) -> dict:
    return prepare_structure(
        candidate_id="REPAIR_CHECK",
        resume_text=text,
        document_blocks=_blocks(text),
    )


def _repair_payload(prepared: dict, *, projects: list[dict], omitted_ids: set[str] | None = None) -> dict:
    omitted = omitted_ids or set()
    all_ids = [str(item["block_id"]) for item in prepared["blocks"]]
    project_ids = {
        str(block_id)
        for project in projects
        for key in ("title_block_ids", "content_block_ids")
        for block_id in project[key]
    }
    sections = [
        block_id
        for block_id in all_ids
        if block_id not in project_ids and block_id not in omitted
    ]
    return {
        "sections": [{"section_type": "experience", "block_ids": sections}],
        "projects": projects,
        "unresolved_block_ids": [],
        "_repair_scope_block_ids": all_ids,
    }


def test_llm_repair_request_is_scope_limited_and_contains_only_needed_block_fields(monkeypatch) -> None:
    text = "\n".join([
        "教育经历",
        "示例大学 本科",
        "项目经历",
        "项目A 2024.03-2024.05",
        "负责实现检索链路并完成测试",
    ])
    prepared = _repair_prepared(text)
    deterministic = resume_ir_from_structure({"status": "passed", "resume_ir": prepared["resumeIr"]})
    captured: dict = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return ({"sections": [], "projects": [], "unresolved_block_ids": []}, {"status": "success"})

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.llm_repair.call_json_llm",
        fake_call,
    )
    repair_resume_structure(
        blocks=prepared["blocks"],
        deterministic_ir=deterministic,
        validation=StructureValidation(
            accepted=False,
            errors=[],
            warnings=[],
            repair_scope="experience_section",
        ),
        llm_config=None,
    )

    request = json.loads(captured["messages"][1]["content"])
    source_ids = {item["block_id"] for item in request["source_blocks"]}
    assert all(set(item) == {"block_id", "order", "text"} for item in request["source_blocks"])
    assert "B_001" not in source_ids and "B_002" not in source_ids
    draft_ids = {
        block_id
        for section in request["rule_draft"]["sections"]
        for block_id in section["block_ids"]
    }
    draft_ids.update(
        block_id
        for project in request["rule_draft"]["projects"]
        for key in ("title_block_ids", "content_block_ids")
        for block_id in project[key]
    )
    assert draft_ids <= source_ids
    assert all(
        set(project) == {"title_block_ids", "content_block_ids"}
        for project in request["rule_draft"]["projects"]
    )


def test_llm_repair_rejects_omitted_scope_block() -> None:
    prepared = _repair_prepared("项目经历\n项目A\n负责实现检索链路")
    all_ids = [str(item["block_id"]) for item in prepared["blocks"]]
    payload = _repair_payload(
        prepared,
        projects=[{"title_block_ids": [all_ids[1]], "content_block_ids": [all_ids[2]]}],
        omitted_ids={all_ids[0]},
    )
    with pytest.raises(ValueError, match="repair_scope_blocks_missing"):
        assemble_repaired_ir(
            candidate_id="REPAIR_CHECK",
            resume_text=prepared["resumeText"],
            blocks=prepared["blocks"],
            payload=payload,
            deterministic_ir=resume_ir_from_structure({"status": "passed", "resume_ir": prepared["resumeIr"]}),
            repair_trace=[],
        )


def test_llm_repair_rejects_project_body_before_title() -> None:
    prepared = _repair_prepared("项目经历\n负责实现检索链路\n项目A")
    all_ids = [str(item["block_id"]) for item in prepared["blocks"]]
    payload = _repair_payload(
        prepared,
        projects=[{"title_block_ids": [all_ids[2]], "content_block_ids": [all_ids[1]]}],
    )
    with pytest.raises(ValueError, match="title_must_precede_content"):
        assemble_repaired_ir(
            candidate_id="REPAIR_CHECK",
            resume_text=prepared["resumeText"],
            blocks=prepared["blocks"],
            payload=payload,
            deterministic_ir=resume_ir_from_structure({"status": "passed", "resume_ir": prepared["resumeIr"]}),
            repair_trace=[],
        )


def test_llm_repair_rejects_field_label_as_project_title() -> None:
    prepared = _repair_prepared("项目经历\n项目描述\n负责实现检索链路")
    all_ids = [str(item["block_id"]) for item in prepared["blocks"]]
    payload = _repair_payload(
        prepared,
        projects=[{"title_block_ids": [all_ids[1]], "content_block_ids": [all_ids[2]]}],
    )
    with pytest.raises(ValueError, match="project_title_is_field_label"):
        assemble_repaired_ir(
            candidate_id="REPAIR_CHECK",
            resume_text=prepared["resumeText"],
            blocks=prepared["blocks"],
            payload=payload,
            deterministic_ir=resume_ir_from_structure({"status": "passed", "resume_ir": prepared["resumeIr"]}),
            repair_trace=[],
        )


def test_llm_repair_cannot_use_education_block_as_project_title() -> None:
    text = "\n".join([
        "教育经历",
        "四川大学 机械工程 本科",
        "项目经历",
        "智能检测系统",
        "负责设计并完成验证",
    ])
    prepared = prepare_structure(
        candidate_id="EDU_PROTECT",
        resume_text=text,
        document_blocks=_blocks(text),
    )
    deterministic = resume_ir_from_structure({
        "status": "passed",
        "resume_ir": prepared["resumeIr"],
    })
    all_ids = [str(item["block_id"]) for item in prepared["blocks"]]
    payload = {
        "sections": [
            {"section_type": "education", "block_ids": [all_ids[0], all_ids[1]]},
            {"section_type": "experience", "block_ids": all_ids[2:]},
        ],
        "projects": [{
            "title_block_ids": [all_ids[1]],
            "content_block_ids": [all_ids[3], all_ids[4]],
        }],
        "unresolved_block_ids": [],
        "_repair_scope_block_ids": all_ids,
    }

    repaired = assemble_repaired_ir(
        candidate_id="EDU_PROTECT",
        resume_text=text,
        blocks=prepared["blocks"],
        payload=payload,
        deterministic_ir=deterministic,
        repair_trace=[],
    )

    education_span = next(
        span for span in repaired.candidate_spans if span.source_block_ids == [all_ids[1]]
    )
    assert education_span.section == "education"
    assert education_span.experience_unit_id is None
    assert repaired.experience_units[0].title != "四川大学 机械工程 本科"


def test_work_unit_llm_can_merge_multiple_source_bullets(monkeypatch) -> None:
    monkeypatch.setenv("RECRUIT_LLM_ENABLED", "true")
    text = "\n".join(
        [
            "项目经历",
            "旅行规划助手 2025.01-2025.02",
            "设计多智能体协作架构",
            "增加容错降级与保底行程",
        ]
    )
    resume_ir = build_resume_ir("WU_MERGE", text)
    first_bullet = resume_ir.source_bullets[0]
    second_bullet = SourceBullet(
        source_bullet_id="SB_002",
        experience_unit_id=first_bullet.experience_unit_id,
        raw_text="增加容错降级与保底行程",
        source_line_start=4,
        source_line_end=4,
        work_unit_ids=[],
        source_block_ids=["B_004"],
    )
    resume_ir.source_bullets.append(second_bullet)
    resume_ir.experience_units[0].source_bullet_ids.append(
        second_bullet.source_bullet_id
    )

    def fake_call(**kwargs):
        del kwargs
        bullets = resume_ir.source_bullets
        return (
            {
                "context_only_bullet_ids": [],
                "work_units": [
                    {
                        "source_refs": [
                            {
                                "bullet_id": bullets[0].source_bullet_id,
                                "quote": bullets[0].raw_text,
                            },
                            {
                                "bullet_id": bullets[1].source_bullet_id,
                                "quote": bullets[1].raw_text,
                            },
                        ]
                    }
                ]
            },
            {"status": "success"},
        )

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.work_unit_extractor.call_json_llm",
        fake_call,
    )
    units, _, error = extract_work_units(
        resume_ir,
        llm_config={
            "enabled": True,
            "workflows": {
                "resume_work_unit_extraction": {"enabled": True}
            },
        },
    )
    assert error is None
    assert units is not None and len(units) == 1
    assert len(units[0].source_refs) == 2
    assert all(
        units[0].work_unit_id in bullet.work_unit_ids
        for bullet in resume_ir.source_bullets
    )


def test_work_unit_llm_rejects_unknown_source_bullet(monkeypatch) -> None:
    monkeypatch.setenv("RECRUIT_LLM_ENABLED", "true")
    text = "\n".join(
        [
            "项目经历",
            "旅行规划助手 2025.01-2025.02",
            "设计多智能体协作架构",
        ]
    )
    resume_ir = build_resume_ir("WU_INVALID", text)

    def fake_call(**kwargs):
        del kwargs
        return (
            {
                "context_only_bullet_ids": [],
                "work_units": [
                    {
                        "source_refs": [
                            {
                                "bullet_id": "SB_NOT_EXISTS",
                            }
                        ]
                    }
                ]
            },
            {"status": "success"},
        )

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.work_unit_extractor.call_json_llm",
        fake_call,
    )
    units, _, error = extract_work_units(
        resume_ir,
        llm_config={
            "enabled": True,
            "workflows": {
                "resume_work_unit_extraction": {"enabled": True}
            },
        },
    )
    assert units is None
    assert "work_unit_response_invalid" in str(error)
    with pytest.raises(LLMResponseError, match="unknown_bullet_id"):
        _extract_project(
            _project_input(
                resume_ir,
                resume_ir.experience_units[0].experience_unit_id,
                resume_ir.experience_units[0].title,
            ),
            {
                "enabled": True,
                "workflows": {"resume_work_unit_extraction": {"enabled": True}},
            },
        )


def test_work_unit_rehydrates_verbatim_evidence_from_bullet_id(monkeypatch) -> None:
    """LLM 只选源 bullet；落库 quote 必须由后端回填为冻结原文。"""
    monkeypatch.setenv("RECRUIT_LLM_ENABLED", "true")
    resume_ir = build_resume_ir(
        "WU_STABLE_REF",
        "\n".join(
            [
                "项目经历",
                "旅行规划助手 2025.01-2025.02",
                "构建多智能体协作与容错链路。",
                "完成失败降级策略与回归验证。",
            ]
        ),
    )
    first, second = resume_ir.source_bullets

    def fake_call(**kwargs):
        del kwargs
        return (
            {
                "work_units": [
                    {
                        "source_refs": [
                            {
                                "bullet_id": first.source_bullet_id,
                                # 即使旧模型附带改写 quote，也必须被后端忽略。
                                "quote": "构建多智能体协作链路",
                            }
                        ]
                    },
                    {"source_refs": [{"bullet_id": second.source_bullet_id}]},
                ],
                "context_only_bullet_ids": [],
            },
            {"status": "success"},
        )

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.work_unit_extractor.call_json_llm",
        fake_call,
    )
    units, _, error = extract_work_units(
        resume_ir,
        llm_config={
            "enabled": True,
            "workflows": {"resume_work_unit_extraction": {"enabled": True}},
        },
    )

    assert error is None
    assert units is not None and len(units) == 2
    assert units[0].source_refs == [
        {"bullet_id": first.source_bullet_id, "quote": first.raw_text}
    ]
    assert units[1].source_refs == [
        {"bullet_id": second.source_bullet_id, "quote": second.raw_text}
    ]
    assert units[0].raw_text == first.raw_text
    assert units[0].source_line_start == first.source_line_start
    assert units[0].source_line_end == first.source_line_end
    assert units[0].source_block_ids == first.source_block_ids


def test_table_rows_are_globally_grouped_into_projects_and_stop_at_new_section() -> None:
    table = """<table>
    <tr><td colspan="2">-- | -- | 2024-05~至今 (1年)</td></tr>
    <tr><td>项目描述</td><td>胎膜吊装系统项目介绍:验证密封性项目职责:负责构建模型并完成仿真</td></tr>
    <tr><td colspan="2">-- | -- | 2024-03~至今 (2年)</td></tr>
    <tr><td>项目描述</td><td>视觉检测系统项目介绍:检测气泡项目职责:负责部署检测模型</td></tr>
    <tr><td colspan="2">在校职务</td></tr>
    <tr><td>职务描述</td><td>负责活动审核</td></tr>
    <tr><td colspan="2">获奖情况</td></tr>
    <tr><td>获奖项</td><td>优秀研究生</td></tr>
    </table>"""
    prepared = prepare_structure(
        candidate_id="TABLE_PROJECTS",
        resume_text="",
        document_blocks=[
            {"block_id": "B_HEAD", "page": 1, "order": 1, "text": "项目经历", "block_type": "text"},
            {"block_id": "B_TABLE", "page": 1, "order": 2, "text": table, "block_type": "table", "bbox": [0, 0, 100, 800]},
        ],
    )

    blocks = prepared["blocks"]
    assert all("<table" not in item["text"] for item in blocks)
    assert any(item.get("field_label") == "项目职责" for item in blocks)
    units = prepared["resumeIr"]["experience_units"]
    assert [item["title"] for item in units] == ["胎膜吊装系统", "视觉检测系统"]
    experience_text = {
        span["text"]
        for span in prepared["resumeIr"]["candidate_spans"]
        if span["experience_unit_id"]
    }
    assert not any("活动审核" in item or "优秀研究生" in item for item in experience_text)


def test_clear_same_page_parser_order_inversion_uses_bbox_order() -> None:
    normalized = _normalize_blocks(
        [
            {"block_id": "B_EDU", "page": 1, "order": 1, "text": "教育经历", "block_type": "text", "bbox": [10, 100, 100, 120]},
            {"block_id": "B_LATE", "page": 2, "order": 2, "text": "实习经历", "block_type": "text", "bbox": [10, 600, 100, 620]},
            {"block_id": "B_EARLY", "page": 2, "order": 3, "text": "某大学 本科", "block_type": "text", "bbox": [10, 20, 100, 40]},
        ],
        "",
    )

    assert [item["block_id"] for item in normalized] == [
        "B_EDU", "B_EARLY", "B_LATE"
    ]


def test_work_unit_llm_can_exclude_project_context_bullet(monkeypatch) -> None:
    monkeypatch.setenv("RECRUIT_LLM_ENABLED", "true")
    resume_ir = build_resume_ir(
        "WU_CONTEXT",
        "\n".join(
            [
                "项目经历",
                "旅行规划助手 2025.01-2025.02",
                "面向用户提供天气、酒店和行程推荐",
                "构建多智能体协作与容错链路",
            ]
        ),
    )
    # 这两条原文均已由确定性结构层创建：第一条是项目背景，第二条是候选人动作。
    # 直接使用冻结 bullet，不能再手工追加同义内容，否则会破坏“每个源 bullet 恰好一次”
    # 的 WorkUnit 分配约束。
    context, first = resume_ir.source_bullets

    def fake_call(**kwargs):
        del kwargs
        return (
            {
                "work_units": [
                    {
                        "source_refs": [
                            {
                                "bullet_id": first.source_bullet_id,
                                "quote": first.raw_text,
                            }
                        ]
                    }
                ],
                "context_only_bullet_ids": [context.source_bullet_id],
            },
            {"status": "success"},
        )

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.work_unit_extractor.call_json_llm",
        fake_call,
    )
    units, _, error = extract_work_units(
        resume_ir,
        llm_config={
            "enabled": True,
            "workflows": {
                "resume_work_unit_extraction": {"enabled": True}
            },
        },
    )
    assert error is None
    assert units is not None and len(units) == 1
    assert context.work_unit_ids == []


def test_work_unit_llm_can_publish_all_context_result(monkeypatch) -> None:
    """所有来源都仅为项目背景时，空 WorkUnit 是正常结果而不是结构化失败。"""
    monkeypatch.setenv("RECRUIT_LLM_ENABLED", "true")
    resume_ir = build_resume_ir(
        "WU_ALL_CONTEXT",
        "\n".join(
            [
                "项目经历",
                "校园智慧服务平台 2025.01-2025.02",
                "平台面向学生提供课表、通知和活动查询功能。",
            ]
        ),
    )
    bullet_ids = [item.source_bullet_id for item in resume_ir.source_bullets]

    def fake_call(**kwargs):
        del kwargs
        return (
            {
                "work_units": [],
                "context_only_bullet_ids": bullet_ids,
            },
            {"status": "success"},
        )

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.work_unit_extractor.call_json_llm",
        fake_call,
    )
    units, _, error = extract_work_units(
        resume_ir,
        llm_config={
            "enabled": True,
            "workflows": {"resume_work_unit_extraction": {"enabled": True}},
        },
    )

    assert _schema()["properties"]["work_units"].get("minItems") is None
    assert error is None
    assert units == []
    assert all(item.work_unit_ids == [] for item in resume_ir.source_bullets)


def test_work_unit_llm_promotes_actionable_bullet_from_context(monkeypatch) -> None:
    monkeypatch.setenv("RECRUIT_LLM_ENABLED", "true")
    resume_ir = build_resume_ir(
        "WU_ACTION_AS_CONTEXT",
        "项目经历\n智能服务平台 2025.01-2025.02\n负责构建检索链路并完成上线",
    )
    bullet_id = resume_ir.source_bullets[0].source_bullet_id

    monkeypatch.setattr(
        "recruitment_ai_core.resume_structuring.work_unit_extractor.call_json_llm",
        lambda **_: ({
            "work_units": [],
            "context_only_bullet_ids": [bullet_id],
        }, {"status": "success"}),
    )

    units, _, error = extract_work_units(
        resume_ir,
        llm_config={
            "enabled": True,
            "workflows": {"resume_work_unit_extraction": {"enabled": True}},
        },
    )

    assert error is None
    assert units is not None and len(units) == 1
    assert units[0].source_refs == [{
        "bullet_id": bullet_id,
        "quote": resume_ir.source_bullets[0].raw_text,
    }]


def test_work_unit_failure_preserves_explicit_duty_as_verbatim_unit() -> None:
    resume_ir = build_resume_ir(
        "WU_DEGRADED",
        "项目经历\n智能检测系统\n项目职责：采集振动信号并完成特征提取",
    )
    project = _project_input(
        resume_ir,
        resume_ir.experience_units[0].experience_unit_id,
        resume_ir.experience_units[0].title,
    )

    units = build_degraded_project_work_units(project)

    assert len(units) == 1
    assert units[0].raw_text == "项目职责：采集振动信号并完成特征提取"
    assert units[0].source_refs == [{
        "bullet_id": resume_ir.source_bullets[0].source_bullet_id,
        "quote": "项目职责：采集振动信号并完成特征提取",
    }]

def test_published_profile_contains_only_compact_nested_evidence() -> None:
    """发布画像只保留候选人事实、嵌套经历和扁平技能声明。"""
    from recruitment_ai_core.screening_scoring.work_unit_structurer import build_resume_profile

    resume_ir = build_resume_ir("CAND_002", "项目经历\n项目 B\n负责接口开发")
    resume_ir.source_bullets = [
        SourceBullet("B_1", "EXP_001", "负责接口开发", 3, 3, [], ["BLOCK_001"]),
    ]
    resume_ir.candidate_facts = {"education_records": [], "relevant_experience_years": {"value": None, "source_refs": []}}
    resume_ir.scorable_work_units = [
        ScorableWorkUnit(
            work_unit_id="EXP_001_WU_001",
            project_id="EXP_001",
            source_refs=[{"bullet_id": "B_1", "quote": "负责接口开发"}],
        )
    ]
    resume_ir.source_bullets[0].work_unit_ids = ["EXP_001_WU_001"]

    profile = build_resume_profile(resume_ir)

    assert {"candidate_facts", "experience_units", "skill_claims"} <= set(profile)
    assert not {
        "education_facts", "source_bullets", "scorable_work_units",
        "project_context_items", "skill_statements", "sections",
    }.intersection(profile)
    assert profile["experience_units"][0]["work_units"][0]["raw_text"] == "负责接口开发"
