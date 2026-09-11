from __future__ import annotations

import re

from .section_resolver import resolve_section_anchor

from .contracts import (
    CandidateSpan,
    ExperienceUnit,
    ProjectContextItem,
    ScorableWorkUnit,
    SourceBullet,
    VerifiedResumeIR,
)
from .fact_kind import resolve_fact_kind
from .resume_input_quality import evaluate_resume_input_quality
from .text_normalizer import normalize_text, sha256_text, split_atoms


# 标准招聘表单会把岗位、证明人、获奖时间等字段拆成独立的、加粗的短行。它们在
# 视觉上很像项目标题，但并不代表一个可评分项目；这里集中维护这些字段前缀，避免
# 与项目标题规则分散后再次发生“表单字段被识别为项目”的回归。
_EXPERIENCE_FIELD_PREFIXES = (
    "项目名称",
    "项目描述",
    "项目简介",
    "项目介绍",
    "项目概述",
    "项目内容",
    "项目职责",
    "项目中职责",
    "项目角色",
    "项目成果",
    "项目时间",
    "项目起止时间",
    "项目单位",
    "项目类型",
    "实习内容",
    "实习单位",
    "工作单位",
    "工作内容",
    "主要工作",
    "核心工作",
    "主要贡献",
    "岗位",
    "职位",
    "职务",
    "部门",
    "证明人",
    "联系人",
    "联系电话",
    "技术栈",
    "开发环境",
    "工作地点",
    "获奖时间",
    "获奖项",
    "获奖级别",
    "获奖描述",
    "上传证明材料",
    "作者顺序",
    "所属期刊",
)

_PROJECT_NAME_CUES = (
    "项目",
    "系统",
    "平台",
    "课题",
    "工具",
    "软件",
    "应用",
    "服务",
    "引擎",
    "助手",
    "机器人",
    "智能体",
    "网站",
    "小程序",
    "算法",
    "模型",
    "课设",
    "研发",
    "实习",
    "科研",
    "课程",
    "竞赛",
    "论文",
)

# 这些内容只提供项目背景或表单管理信息，不是候选人的动作、交付物或结果。
# 它们应进入 ProjectContextItem，绝不能因换行或表单版式成为评分证据。
_CONTEXT_ONLY_FIELD_PREFIXES = (
    "项目名称",
    "项目描述",
    "项目简介",
    "项目介绍",
    "项目概述",
    "项目时间",
    "项目起止时间",
    "项目单位",
    "项目类型",
    "实习单位",
    "工作单位",
    "岗位",
    "职位",
    "职务",
    "部门",
    "证明人",
    "联系人",
    "联系电话",
    "技术栈",
    "开发环境",
    "工作地点",
)


def _redact(text: str) -> str:
    # Require a dotted mail domain.  The former broad pattern treated metrics
    # such as ``MRR@5`` as email addresses and corrupted scoreable evidence.
    redacted = re.sub(
        r"(?<![\w.+-])[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,63}(?![\w.-])",
        "[email]",
        text,
    )
    redacted = re.sub(r"1[3-9]\d{9}", "[phone]", redacted)
    return redacted


def build_resume_ir(
    candidate_id: str,
    resume_text: str,
    *,
    project_title_line_numbers: set[int] | None = None,
    section_by_line_number: dict[int, str] | None = None,
) -> VerifiedResumeIR:
    normalized = normalize_text(resume_text)
    redacted = _redact(normalized)
    atoms = _group_atoms_by_source_line(split_atoms(redacted))
    has_section_headers = any(_section_header(text) for _, text in atoms)
    project_boundary_lines = _experience_boundary_lines(
        atoms,
        section_by_line_number=section_by_line_number or {},
        layout_hints=project_title_line_numbers or set(),
    )
    spans: list[CandidateSpan] = []
    experience_index = 1
    current_experience_id = "EXP_001"
    experience_titles: dict[str, str] = {current_experience_id: "主要项目经历"}
    experience_fact_counts: dict[str, int] = {current_experience_id: 0}
    current_section = "other" if has_section_headers else "experience"
    section_headers_found: list[str] = []
    for idx, (line_no, text) in enumerate(atoms, start=1):
        next_text = atoms[idx][1] if idx < len(atoms) else ""
        fact_kind = resolve_fact_kind(text)
        header_section = _section_header(text)
        if header_section:
            current_section = header_section
            section_headers_found.append(header_section)
        resolved_section = (section_by_line_number or {}).get(line_no)
        if resolved_section:
            current_section = resolved_section
        # 有明确段落标题时，以段落标题为准。否则表单里出现“本科”“专业”等词时，
        # 会把已经进入的项目/实习段错误切回 education，继而制造伪造的段落回跳。
        section = (
            "education"
            if fact_kind == "education_entry"
            and (not has_section_headers or current_section in {"other", "education"})
            else current_section
        )
        is_header = header_section is not None
        explicit_project_title = (
            _explicit_project_title(text) if section == "experience" else None
        )
        # Project boundaries were selected from the complete section before
        # this assembly pass.  The loop only groups the chosen ranges; it no
        # longer makes an irreversible boundary decision from one line alone.
        is_project_title = section == "experience" and line_no in project_boundary_lines
        if is_project_title:
            if experience_fact_counts.get(current_experience_id, 0) > 0:
                experience_index += 1
                current_experience_id = f"EXP_{experience_index:03d}"
                experience_fact_counts[current_experience_id] = 0
            experience_titles[current_experience_id] = (
                explicit_project_title
                or (
                    _derived_title_from_context(next_text)
                    if _is_placeholder_date_row(text)
                    else None
                )
                or text
            )
        experience_unit_id = (
            current_experience_id
            if section == "experience" and not is_header and not is_project_title
            else None
        )
        spans.append(
            CandidateSpan(
                span_id=f"SPAN_{idx:03d}",
                section=section,
                # 短行只是“标题候选”，并不等同于项目标题。只有通过本函数上方
                # 的项目边界规则后才保留 experience_title，避免表单短字段污染
                # 后续校验与质量报告。
                rough_type=(
                    "experience_title"
                    if is_project_title
                    else ("other_text" if fact_kind == "experience_title" else fact_kind)
                ),
                experience_unit_id=experience_unit_id,
                source_line_start=line_no,
                source_line_end=line_no,
                subspan_index=idx,
                text=text,
            )
        )
        if experience_unit_id:
            experience_fact_counts[experience_unit_id] = experience_fact_counts.get(experience_unit_id, 0) + 1
    if not spans and redacted:
        spans.append(
            CandidateSpan(
                span_id="SPAN_001",
                section="experience",
                rough_type=resolve_fact_kind(redacted),
                experience_unit_id="EXP_001",
                source_line_start=1,
                source_line_end=1,
                subspan_index=1,
                text=redacted,
            )
        )
    source_bullets: list[SourceBullet] = []
    scorable_spans = [
        item
        for item in spans
        if item.experience_unit_id and _is_scorable_experience_text(item.text)
    ]
    for idx, span in enumerate(scorable_spans, start=1):
        source_bullet_id = f"SB_{idx:03d}"
        source_bullets.append(
            SourceBullet(
                source_bullet_id=source_bullet_id,
                experience_unit_id=span.experience_unit_id or "EXP_001",
                raw_text=span.text,
                source_line_start=span.source_line_start,
                source_line_end=span.source_line_end,
                work_unit_ids=[],
            )
        )
    experience_ids = list(dict.fromkeys(span.experience_unit_id for span in spans if span.experience_unit_id))
    units = [
        ExperienceUnit(
            experience_id,
            experience_titles.get(experience_id, "项目经历"),
            [span.span_id for span in spans if span.experience_unit_id == experience_id],
            [item.source_bullet_id for item in source_bullets if item.experience_unit_id == experience_id],
        )
        for experience_id in experience_ids
    ]
    project_context_items = _project_context_items(units, spans, source_bullets)
    quality_report = evaluate_resume_input_quality(
        redacted,
        spans=spans,
        experience_units=units,
        source_bullets=source_bullets,
        section_headers_found=section_headers_found,
    )
    return VerifiedResumeIR(
        resume_ir_version="verified_resume_ir_v1_3",
        candidate_id=candidate_id,
        resume_raw_sha256=sha256_text(resume_text),
        resume_redacted_sha256=sha256_text(redacted),
        candidate_spans=spans,
        experience_units=units,
        source_bullets=source_bullets,
        scorable_work_units=[],
        redaction_warnings=[],
        project_context_items=project_context_items,
        input_quality_report=quality_report,
        structuring_provenance={
            "mode": "deterministic_resume_experience_v1_1",
            "primary_evidence_structure": "ExperienceUnit->SourceBullet; WorkUnit pending LLM extraction",
            "candidate_span_usage": "compatibility_source_span_only",
            "work_unit_policy": "no deterministic WorkUnit construction",
        },
    )


def _project_context_items(
    units: list[ExperienceUnit],
    spans: list[CandidateSpan],
    source_bullets: list[SourceBullet],
) -> list[ProjectContextItem]:
    scorable_keys = {
        (item.experience_unit_id, item.source_line_start, item.source_line_end, item.raw_text)
        for item in source_bullets
    }
    output: list[ProjectContextItem] = []
    used_title_spans: set[str] = set()
    spans_by_id = {item.span_id: item for item in spans}
    for unit in units:
        unit_spans = [spans_by_id[item] for item in unit.span_ids if item in spans_by_id]
        first_content_line = min(
            (item.source_line_start for item in unit_spans),
            default=10**9,
        )
        preceding_title_spans = [
            span
            for span in spans
            if span.span_id not in used_title_spans
            and span.section == "experience"
            and span.rough_type == "experience_title"
            and span.experience_unit_id is None
            and span.source_line_start < first_content_line
        ]
        # Record ownership is positional: the nearest preceding compatible title
        # owns the record. Text containment is only a fallback for legacy input.
        title_span = (
            max(preceding_title_spans, key=lambda item: item.source_line_start)
            if preceding_title_spans
            else next(
            (
                span
                for span in spans
                if span.span_id not in used_title_spans
                and span.section == "experience"
                and (
                    span.text == unit.title
                    or _explicit_project_title(span.text) == unit.title
                    or (
                        unit.title not in {"项目经历", "主要项目经历"}
                        and unit.title in span.text
                    )
                )
            ),
            None,
            )
        )
        if title_span:
            used_title_spans.add(title_span.span_id)
            output.append(
                ProjectContextItem(
                    context_id=f"CTX_{len(output) + 1:03d}",
                    experience_unit_id=unit.experience_unit_id,
                    context_type="project_title",
                    text=title_span.text,
                    source_line_start=title_span.source_line_start,
                    source_line_end=title_span.source_line_end,
                )
            )
        for span_id in unit.span_ids:
            span = spans_by_id[span_id]
            if title_span is not None and span.span_id == title_span.span_id:
                continue
            key = (
                unit.experience_unit_id,
                span.source_line_start,
                span.source_line_end,
                span.text,
            )
            if key in scorable_keys:
                continue
            output.append(
                ProjectContextItem(
                    context_id=f"CTX_{len(output) + 1:03d}",
                    experience_unit_id=unit.experience_unit_id,
                    context_type=_context_type(span.text),
                    text=span.text,
                    source_line_start=span.source_line_start,
                    source_line_end=span.source_line_end,
                )
            )
    return output


def _context_type(text: str) -> str:
    if _is_experience_metadata(text):
        return "project_date"
    label = re.split(r"[：:]", text, maxsplit=1)[0].strip()
    if label in {"项目简介", "项目介绍", "项目描述", "项目概述", "实习简介"}:
        return "project_description"
    if label == "技术栈":
        return "tech_stack"
    if label == "开发环境":
        return "development_environment"
    return "other_context"


def _group_atoms_by_source_line(atoms: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Preserve the document rule that an action and its direct result stay in one WorkUnit."""
    grouped: dict[int, list[str]] = {}
    for line_no, text in atoms:
        grouped.setdefault(line_no, []).append(text)
    return [(line_no, "，".join(parts)) for line_no, parts in grouped.items()]


def _is_project_title(
    text: str,
    fact_kind: str,
    next_text: str = "",
    *,
    layout_hint: bool = False,
) -> bool:
    """判断一行是否足以开启新的经历单元。

    版式加粗、字号等信息只作为辅助证据，不能单独把一行表单字段升级为项目标题。
    这样既保留普通简历的无日期项目标题识别，也避免标准表格式简历被大量拆碎。
    """
    if text in {"项目经历", "工作经历", "实习经历", "科研经历"}:
        return False
    if _section_header(text):
        return False
    # Check this before the generic date-metadata rule.  A placeholder date row
    # is normally context, but when it is followed by a project description it
    # is the only reliable boundary in a standard table-form resume.  This
    # semantic pair also works for Markdown/vision parsers that have no bbox.
    if _is_placeholder_date_row(text) and _starts_with_project_context(next_text):
        return True
    if _is_experience_metadata(text):
        return False
    if _looks_like_experience_field(text):
        return False
    if _looks_like_narrative_sentence(text):
        return False
    label = re.split(r"[：:]", text, maxsplit=1)[0].strip()
    if label in {
        "项目简介",
        "项目介绍",
        "项目描述",
        "实习简介",
        "工作内容",
        "主要工作",
        "核心工作",
        "主要贡献",
        "技术亮点",
        "技术栈",
        "主动触发",
        "定时触发",
        "被动触发",
    }:
        return False
    if text.rstrip().endswith(("：", ":")):
        return False
    if _project_title_error_code(text):
        return False
    if _contains_date_range(text):
        # 日期只能辅助确认边界；去掉日期后仍须存在项目、公司、实习等名称主体。
        return _has_experience_identity(text)
    next_label = re.split(r"[：:]", next_text, maxsplit=1)[0].strip()
    if _is_experience_metadata(next_text):
        return _looks_like_project_name(text)
    if next_label in {
        "项目简介",
        "项目描述",
        "实习简介",
        "工作内容",
        "主要工作",
        "核心工作",
        "主要贡献",
        "技术栈",
    }:
        return _looks_like_project_name(text)
    action_cues = ["负责", "主导", "参与", "实现", "开发", "构建", "设计", "完成", "优化"]
    if any(cue in text for cue in action_cues):
        return False
    # resolve_fact_kind 会把所有短行标为 experience_title，不能直接作为充分条件。
    # 只有项目语义或“版式提示 + 项目语义”同时成立时，才新建经历单元。
    return (
        _looks_like_project_name(text)
        and (fact_kind == "experience_title" or layout_hint)
    )


def _experience_boundary_lines(
    atoms: list[tuple[int, str]],
    *,
    section_by_line_number: dict[int, str],
    layout_hints: set[int],
) -> set[int]:
    """Choose all project boundaries before assembling ExperienceUnits."""
    output: set[int] = set()
    current_section = (
        "other" if any(_section_header(text) for _, text in atoms) else "experience"
    )
    for index, (line_no, text) in enumerate(atoms):
        header = _section_header(text)
        if header:
            current_section = header
        current_section = section_by_line_number.get(line_no, current_section)
        if current_section != "experience" or header is not None:
            continue
        next_text = atoms[index + 1][1] if index + 1 < len(atoms) else ""
        if _explicit_project_title(text) is not None or _is_project_title(
            text,
            resolve_fact_kind(text),
            next_text,
            layout_hint=line_no in layout_hints,
        ):
            output.add(line_no)
    return output


def _is_placeholder_date_row(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not re.search(r"(?:19|20)\d{2}", compact):
        return False
    meaningful = re.sub(r"[|｜\-_—–~～﹣－至到今0-9./年月个()（）]", "", compact)
    return not meaningful


def _starts_with_project_context(text: str) -> bool:
    compact = re.sub(r"[\s：:]+", "", text)
    return any(
        compact.startswith(prefix)
        for prefix in ("项目描述", "项目简介", "项目介绍", "项目概述", "项目名称")
    )


def _derived_title_from_context(text: str) -> str | None:
    if not _starts_with_project_context(text):
        return None
    match = re.match(
        r"^\s*(?:项目描述|项目简介|项目介绍|项目概述|项目名称)\s*[：:]?\s*(.+?)\s*$",
        text,
    )
    if match is None:
        return None
    value = match.group(1).strip(" -—:：")
    if not value:
        return None
    # MinerU can flatten adjacent form fields into one text Block.  Stop the
    # display title at the next explicit field marker; the remaining text stays
    # in context/evidence and must not become part of the project name.
    value = re.split(
        r"(?:项目介绍|项目简介|项目概述|项目职责|项目中职责|项目成果)\s*[：:]",
        value,
        maxsplit=1,
    )[0].strip(" -—:：")
    if not value:
        return None
    quoted = re.search(r"(?:本项目为|项目为)?[“\"]([^”\"]{2,80})[”\"]", value)
    if quoted:
        return quoted.group(1).strip()
    # Keep the title an exact prefix of the source line; this is a display label,
    # not a generated fact.  Long descriptions remain available as context.
    return re.split(
        r"(?<=[。！？!?；;])|[，,](?=(?:作为|本项目|该项目|项目介绍))",
        value,
        maxsplit=1,
    )[0].strip()[:80] or None


def _looks_like_experience_field(text: str) -> bool:
    compact = re.sub(r"[\s：:/／]+", "", text)
    return any(
        compact == prefix or compact.startswith(prefix)
        for prefix in _EXPERIENCE_FIELD_PREFIXES
    )


def _looks_like_project_name(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 4 or len(compact) > 80 or _looks_like_experience_field(compact):
        return False
    return any(cue in compact for cue in _PROJECT_NAME_CUES) or bool(
        re.search(r"(?:大学|学院|公司|集团|研究院|实验室|工作室|事务所)", compact)
    )


def _looks_like_narrative_sentence(text: str) -> bool:
    """Reject short prose that merely mentions a project-like noun."""
    stripped = text.strip()
    if re.search(r"[。！？!?；;，,]", stripped) and re.search(
        r"(?:负责|参与|完成|实现|开发|设计|撰写|正在|目前|相关|主要|通过|基于)",
        stripped,
    ):
        return True
    return bool(re.match(
        r"^(?:负责|参与|完成|实现|开发|设计|撰写|正在|目前|通过|基于)",
        stripped,
    ))


def _has_experience_identity(text: str) -> bool:
    without_date = re.sub(
        r"(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?"
        r"\s*[-—–~～﹣－至到]+\s*(?:(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?|至今)",
        " ",
        text,
    )
    name = re.sub(r"[|\-_—–~～﹣－\s()（）]+", "", without_date)
    return (
        len(name) >= 2
        and bool(re.search(r"[\u4e00-\u9fffA-Za-z]", name))
        and (
            any(cue in name for cue in _PROJECT_NAME_CUES)
            or bool(re.search(r"(?:公司|集团|研究院|实验室|工作室|中心|事务所)", name))
        )
    )


def _project_title_error_code(text: str) -> str | None:
    """返回可客观验证的伪标题类型，供确定性结构和LLM修复共用。"""
    stripped = text.strip()
    without_date = re.sub(
        r"(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?"
        r"\s*[-—–~～﹣－至到]+\s*(?:(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?|至今)",
        " ",
        stripped,
    )
    meaningful = re.sub(r"[|\-_—–~～﹣－\s()（）]+", "", without_date)
    if "|" in stripped and not re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", meaningful):
        return "project_title_is_table_fragment"
    if _contains_date_range(stripped) and not re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", meaningful):
        return "project_title_is_date_only"
    # Action words inside organization names (for example, 建筑设计研究院) are
    # nouns rather than work statements.  Treat them as actions only at the
    # beginning of the line or of a separated table segment.
    if re.search(
        r"(?:^|[，,；;|｜])\s*(?:负责|主导|参与|设计|开发|实现|构建|优化|完成|建立|分析|撰写)",
        stripped,
    ):
        return "project_title_is_action_sentence"
    if re.search(r"(?:提升|降低|增长|减少|节省|上线|交付|获奖|准确率|召回率)", stripped):
        return "project_title_is_result_fragment"
    compact = re.sub(r"\s+", "", stripped)
    if len(compact) > 80 or stripped.endswith(("。", "；", ";", "，", ",")):
        return "project_title_is_result_fragment"
    return None


def _explicit_project_title(text: str) -> str | None:
    match = re.match(r"^\s*(?:项目名称|项目名)\s*[：:]?\s*(.+?)\s*$", text)
    if match is None:
        return None
    title = match.group(1).strip()
    compact = re.sub(r"\s+", "", title)
    return (
        title
        if (
            2 <= len(compact) <= 80
            and not _looks_like_experience_field(title)
            and _project_title_error_code(title) is None
        )
        else None
    )


def _section_header(text: str) -> str | None:
    resolved = resolve_section_anchor(text)
    if resolved is not None:
        return resolved
    normalized = re.sub(r"[\s：:／/]+", "", text)
    sections = {
        "基础信息": "profile",
        "个人信息": "profile",
        "基本信息": "profile",
        "教育背景": "education",
        "教育经历": "education",
        "专业技能": "skills",
        "技能清单": "skills",
        "技能专长": "skills",
        "核心技能": "skills",
        "技术技能": "skills",
        "软件能力": "skills",
        "工具能力": "skills",
        "技术能力": "skills",
        "专业能力": "skills",
        "计算机技能": "skills",
        "软件技能": "skills",
        "熟悉工具": "skills",
        "工作经历": "experience",
        "实习经历": "experience",
        "实习经验": "experience",
        "项目经历": "experience",
        "项目经验": "experience",
        "项目实践": "experience",
        "在校项目": "experience",
        "校园项目": "experience",
        "个人项目": "experience",
        "实践经历": "experience",
        "科研经历": "experience",
        "奖励荣誉": "honors",
        "荣誉奖项": "honors",
        "获奖经历": "honors",
        "在校荣誉": "honors",
        "获奖情况": "honors",
        "奖励情况": "honors",
        "荣誉情况": "honors",
        "论文专著": "research",
        "发表论文": "research",
        "学术成果": "research",
        "科研成果": "research",
        "专利成果": "research",
        "在校职务": "other",
        "学生工作": "other",
        "校园经历": "other",
        "社会职务": "other",
        "家庭情况": "profile",
        "附加信息": "profile",
        "其他信息": "profile",
        "简历附件": "profile",
        "自我评价": "profile",
        "求职意向": "profile",
        "资格证书": "qualification",
    }
    exact = sections.get(normalized)
    if exact:
        return exact
    if re.match(r"^(?:技能|软件|工具|软件能力|工具能力|技术能力|专业能力|计算机技能|软件技能|熟悉工具)[：:].+", text.strip()):
        return "skills"
    for prefix, section in (
        ("获奖经历", "honors"),
        ("获奖情况", "honors"),
        ("奖励情况", "honors"),
        ("奖励荣誉", "honors"),
        ("荣誉奖项", "honors"),
        ("论文专著", "research"),
        ("发表论文", "research"),
        ("学术成果", "research"),
        ("在校职务", "other"),
        ("学生工作", "other"),
        ("家庭情况", "profile"),
        ("附加信息", "profile"),
        ("简历附件", "profile"),
        ("专业技能", "skills"),
        ("技能专长", "skills"),
        ("软件能力", "skills"),
        ("工具能力", "skills"),
        ("技术能力", "skills"),
        ("专业能力", "skills"),
        ("计算机技能", "skills"),
        ("软件技能", "skills"),
        ("熟悉工具", "skills"),
        ("项目经历", "experience"),
        ("项目经验", "experience"),
        ("在校项目", "experience"),
        ("工作经历", "experience"),
        ("实习经历", "experience"),
    ):
        if normalized.startswith(prefix) and len(normalized) <= len(prefix) + 12:
            return section
    return None


def _is_scorable_experience_text(text: str) -> bool:
    """保留候选工作事实，明确的表单字段和项目背景只作为上下文。"""
    stripped = text.strip()
    if not stripped:
        return False
    if _is_experience_metadata(stripped):
        return False
    if _is_explicit_duty_text(stripped):
        return True
    if _starts_with_field_prefix(stripped, _CONTEXT_ONLY_FIELD_PREFIXES):
        return False
    if _is_role_only_field(stripped):
        return False
    label = re.split(r"[：:]", stripped, maxsplit=1)[0].strip()
    context_labels = {
        "项目简介",
        "项目介绍",
        "项目描述",
        "项目概述",
        "实习简介",
        "技术栈",
        "开发环境",
    }
    heading_labels = {
        "工作内容",
        "主要工作",
        "核心工作",
        "主要贡献",
        "技术亮点",
    }
    if label in context_labels:
        return False
    if label in heading_labels and not re.search(r"[：:].+\S", stripped):
        return False
    return not stripped.endswith(("：", ":"))


def _is_explicit_duty_text(text: str) -> bool:
    """Return true only for a labelled field containing substantive work text."""
    match = re.match(
        r"^\s*(?:项目职责|项目中职责|工作职责|工作内容|实习内容|职务描述|"
        r"实践描述|职责描述|主要工作|核心工作|主要贡献)\s*[：:]\s*(\S.+)$",
        text,
    )
    if match is None:
        return False
    return not _is_role_only_field(text)


def _is_role_only_field(text: str) -> bool:
    match = re.match(
        r"^\s*(?:项目中职责|项目职责|项目角色|角色|岗位|职位|职务)\s*[：:]?\s*(.*?)\s*$",
        text,
    )
    if match is None:
        return False
    value = match.group(1).strip()
    if not value:
        return True
    compact = re.sub(r"\s+", "", value)
    if len(compact) <= 24 and re.fullmatch(
        r"(?:项目)?(?:负责人|开发人员|研发人员|设计人员|实施人员|测试人员|成员|组员|"
        r"开发工程师|算法工程师|实习生|工程师|设计师|研究员|技术负责人|独立开发)",
        compact,
    ):
        return True
    if re.search(
        r"(?:负责|主导|参与|实现|开发|构建|设计|完成|优化|分析|撰写|测试|交付)",
        value,
    ):
        return False
    return False


def _is_experience_metadata(text: str) -> bool:
    stripped = text.strip()
    if re.search(r"(?:https?://|www\.|github\.com/)", stripped, flags=re.IGNORECASE):
        return True
    compact = re.sub(r"[\s|]+", "", stripped).strip("-_—–~～﹣－")
    # 招聘表单常把“2024-05～2024-11（6个月）”单列为经历日期。它是项目上下文，
    # 不是新的项目标题；尾随的工期说明必须一并纳入日期元数据判断。
    return bool(
        re.fullmatch(
            r"(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?"
            r"(?:[-—–~～﹣－至到]+(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?|[-—–~～﹣－至到]+至今)"
            r"(?:[（(][^()（）]{1,24}[)）])?",
            compact,
        )
    )


def _contains_date_range(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.search(
            r"(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?"
            r"[-—–~～﹣－至到]+(?:(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?|至今)",
            compact,
        )
    )


def _starts_with_field_prefix(text: str, prefixes: tuple[str, ...]) -> bool:
    compact = re.sub(r"[\s：:/／]+", "", text)
    return any(compact == prefix or compact.startswith(prefix) for prefix in prefixes)
