from __future__ import annotations

"""按项目抽取 WorkUnit 的 LLM 边界。

每个项目独立调用并受并发上限控制，避免一个项目的语义判断影响另一个项目。
返回值仍必须通过原文与结构校验，失败时由上层保留确定性结构并进入降级路径。
"""

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from recruitment_ai_core.execution import map_bounded
from recruitment_ai_core.llm import call_json_llm, load_llm_settings
from recruitment_ai_core.llm.errors import LLMResponseError
from recruitment_ai_core.screening_scoring.contracts import (
    ScorableWorkUnit,
    VerifiedResumeIR,
)


WORKFLOW_NAME = "resume_work_unit_extraction"
SCHEMA_NAME = "resume_work_unit_extraction_v3"


def extract_work_units(
    resume_ir: VerifiedResumeIR,
    *,
    llm_config: dict[str, Any] | None,
) -> tuple[list[ScorableWorkUnit] | None, list[dict[str, Any]], str | None]:
    """从已验证项目经历中抽取可独立评分的完整工作事实。"""
    settings = load_llm_settings(llm_config)
    if not settings.is_enabled_for(WORKFLOW_NAME):
        return None, [settings.trace(WORKFLOW_NAME)], "work_unit_llm_disabled"
    max_concurrency = max(
        1,
        min(
            len(resume_ir.experience_units) or 1,
            int(settings.execution.get("global_llm_max_in_flight", 12)),
        ),
    )
    try:
        inputs = [
            _project_input(resume_ir, unit.experience_unit_id, unit.title)
            for unit in resume_ir.experience_units
            if unit.source_bullet_ids
        ]
        if not inputs:
            _bind_work_units_to_bullets(resume_ir, [])
            return [], [], None
        results = map_bounded(
            "resume_work_unit_extraction",
            inputs,
            lambda item: _extract_project(item, llm_config),
            max_concurrency=max_concurrency,
        )
    except Exception as exc:
        if getattr(exc, "retryable", False):
            raise
        return None, [], f"{type(exc).__name__}:{str(exc)[:300]}"
    units: list[ScorableWorkUnit] = []
    traces: list[dict[str, Any]] = []
    for project_units, project_traces in results:
        units.extend(project_units)
        traces.extend(project_traces)
    # 所有经历均被判为背景信息时，空 WorkUnit 是有效、可发布的事实结果。
    # 初筛会按现有技能声明等其余证据计算，不能因此把整份简历判为失败。
    if not units:
        _bind_work_units_to_bullets(resume_ir, units)
        return units, traces, None
    _bind_work_units_to_bullets(resume_ir, units)
    return units, traces, None


def _extract_project(
    project: dict[str, Any],
    llm_config: dict[str, Any] | None,
) -> tuple[list[ScorableWorkUnit], list[dict[str, Any]]]:
    validation_error = ""
    traces: list[dict[str, Any]] = []
    for attempt in range(1):
        payload = {
            **project,
            **(
                {"previous_validation_error": validation_error}
                if validation_error
                else {}
            ),
        }
        try:
            parsed, trace = call_json_llm(
                workflow_name=WORKFLOW_NAME,
                messages=[
                    {"role": "system", "content": _prompt_text()},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                schema_name=SCHEMA_NAME,
                settings_overrides=llm_config,
                json_schema=_schema(),
            )
            traces.append(
                {
                    **trace,
                    "project_id": project["project_id"],
                    "attempt": attempt + 1,
                }
            )
        except Exception as exc:
            if getattr(exc, "retryable", False):
                raise
            trace = dict(getattr(exc, "llm_trace", {}) or {})
            traces.append(
                {
                    **trace,
                    "project_id": project["project_id"],
                    "attempt": attempt + 1,
                }
            )
            validation_error = (
                f"call_failed:{type(exc).__name__}:{str(exc)[:200]}"
            )
            continue
        if parsed is None:
            validation_error = "llm_disabled"
            continue
        valid, validation_error = _validate_response(parsed, project)
        if valid:
            return _normalize_units(parsed, project), traces
        if validation_error.startswith("actionable_bullet_classified_as_context:"):
            # 这是可安全修复的语义分类错误：模型已经提供了合法 Bullet ID，
            # 只是把动作/结果事实放进了背景列表。逐条提升为最小 WorkUnit，
            # 继续使用冻结原文作为证据，避免一次模型误判阻断整份简历。
            actionable_ids = [
                item
                for item in validation_error.split(":", 1)[1].split(",")
                if item
            ]
            repaired_payload = {
                "work_units": [
                    *list(parsed.get("work_units") or []),
                    *[
                        {"source_refs": [{"bullet_id": bullet_id}]}
                        for bullet_id in actionable_ids
                    ],
                ],
                "context_only_bullet_ids": [
                    str(item)
                    for item in list(parsed.get("context_only_bullet_ids") or [])
                    if str(item) not in set(actionable_ids)
                ],
            }
            repaired_valid, repaired_error = _validate_response(
                repaired_payload, project
            )
            if repaired_valid:
                traces.append({
                    "outcome": "degraded",
                    "resolution_code": "work_unit_actionable_context_promoted",
                    "project_id": project["project_id"],
                    "promoted_bullet_ids": actionable_ids,
                })
                return _normalize_units(repaired_payload, project), traces
            validation_error = repaired_error
    raise LLMResponseError(
        f"work_unit_response_invalid:{project['project_id']}:{validation_error}"
    )


def _project_input(
    resume_ir: VerifiedResumeIR,
    project_id: str,
    project_name: str,
) -> dict[str, Any]:
    bullets = [
        {
            "bullet_id": item.source_bullet_id,
            "text": item.raw_text,
            "source_block_ids": list(item.source_block_ids),
            "source_line_start": item.source_line_start,
            "source_line_end": item.source_line_end,
        }
        for item in resume_ir.source_bullets
        if item.experience_unit_id == project_id
    ]
    if not bullets:
        raise ValueError(f"project_source_bullets_missing:{project_id}")
    return {
        "project_id": project_id,
        "project_name": project_name,
        "project_context": [
            asdict(item)
            for item in resume_ir.project_context_items
            if item.experience_unit_id == project_id
        ],
        "source_bullets": bullets,
    }


def _validate_response(
    payload: dict[str, Any],
    project: dict[str, Any],
) -> tuple[bool, str]:
    rows = payload.get("work_units")
    if not isinstance(rows, list):
        return False, "work_units_not_array"
    bullets = {
        str(item["bullet_id"]): str(item["text"])
        for item in project["source_bullets"]
    }
    referenced_bullets: set[str] = set()
    for row in rows:
        refs = row.get("source_refs") if isinstance(row, dict) else None
        if not isinstance(refs, list) or not refs or len(refs) > 3:
            return False, "invalid_source_refs"
        for ref in refs:
            if not isinstance(ref, dict):
                return False, "invalid_source_ref"
            bullet_id = str(ref.get("bullet_id") or "")
            if bullet_id not in bullets:
                return False, "unknown_bullet_id"
            # LLM 只选择不可伪造的 bullet_id；同一原文只能归入一个 WorkUnit。
            # 逐字证据由下方发布函数从 SourceBullet 原文回填，不接受模型复述。
            if bullet_id in referenced_bullets:
                return False, "duplicate_source_bullet_id"
            referenced_bullets.add(bullet_id)
    context_only = payload.get("context_only_bullet_ids") or []
    if not isinstance(context_only, list):
        return False, "invalid_context_only_bullet_ids"
    context_ids = {str(item) for item in context_only}
    if len(context_ids) != len(context_only):
        return False, "duplicate_context_only_bullet_id"
    if not context_ids <= set(bullets):
        return False, "unknown_context_only_bullet_id"
    if context_ids & referenced_bullets:
        return False, "bullet_has_multiple_assignments"
    actionable_context = sorted(
        bullet_id
        for bullet_id in context_ids
        if _is_actionable_bullet(bullets[bullet_id])
    )
    if actionable_context:
        return False, "actionable_bullet_classified_as_context:" + ",".join(actionable_context)
    missing = set(bullets) - referenced_bullets - context_ids
    if missing:
        return False, "unassigned_source_bullets:" + ",".join(sorted(missing))
    return True, ""


def _is_actionable_bullet(text: str) -> bool:
    """只识别明确动作或结果，不把普通项目背景强行升级为评分事实。"""
    return bool(re.search(
        r"(?:负责|主导|参与|设计|开发|实现|构建|优化|完成|建立|分析|撰写|"
        r"上线|交付|发表|申请专利|获得专利|提升|降低|增长|减少|节省)",
        text,
    ))


def build_degraded_project_work_units(
    project: dict[str, Any],
) -> list[ScorableWorkUnit]:
    """Preserve explicit source-backed work facts when semantic grouping fails.

    No summary or inferred skill is generated. Each accepted SourceBullet becomes
    one minimal WorkUnit whose text and location are rehydrated from frozen input.
    """
    bullets = [
        item
        for item in list(project.get("source_bullets") or [])
        if isinstance(item, dict)
    ]
    selected = [
        item
        for item in bullets
        if _is_actionable_bullet(str(item.get("text") or ""))
        or _is_explicit_duty_bullet(str(item.get("text") or ""))
    ]
    payload = {
        "work_units": [
            {"source_refs": [{"bullet_id": str(item["bullet_id"])}]}
            for item in selected
        ],
        "context_only_bullet_ids": [
            str(item["bullet_id"]) for item in bullets if item not in selected
        ],
    }
    return _normalize_units(payload, project)


def _is_explicit_duty_bullet(text: str) -> bool:
    return bool(re.match(
        r"^\s*(?:项目职责|项目中职责|工作职责|工作内容|实习内容|职务描述|"
        r"实践描述|职责描述|主要工作|核心工作|主要贡献)\s*[：:]\s*\S.+$",
        text,
    ))


def _normalize_units(
    payload: dict[str, Any],
    project: dict[str, Any],
) -> list[ScorableWorkUnit]:
    bullets = {
        str(item["bullet_id"]): item for item in project["source_bullets"]
    }
    output: list[ScorableWorkUnit] = []
    for index, row in enumerate(payload["work_units"], start=1):
        refs = []
        for raw_ref in row["source_refs"]:
            bullet_id = str(raw_ref["bullet_id"])
            # 证据文案由后端从冻结的 SourceBullet 回填，保证落库 quote 始终是
            # 原文的连续逐字片段，不受模型空格、标点或截断习惯影响。
            refs.append(
                {
                    "bullet_id": bullet_id,
                    "quote": str(bullets[bullet_id]["text"]),
                }
            )
        output.append(
            ScorableWorkUnit(
                work_unit_id=f"{project['project_id']}_WU_{index:03d}",
                project_id=str(project["project_id"]),
                raw_text="\n".join(item["quote"] for item in refs),
                source_line_start=min(
                    int(bullets[item["bullet_id"]]["source_line_start"])
                    for item in refs
                ),
                source_line_end=max(
                    int(bullets[item["bullet_id"]]["source_line_end"])
                    for item in refs
                ),
                source_block_ids=list(dict.fromkeys(
                    block_id
                    for item in refs
                    for block_id in list(
                        bullets[item["bullet_id"]].get("source_block_ids") or []
                    )
                )),
                source_refs=refs,
            )
        )
    return output


def _bind_work_units_to_bullets(
    resume_ir: VerifiedResumeIR,
    units: list[ScorableWorkUnit],
) -> None:
    bullets_by_id = {
        item.source_bullet_id: item for item in resume_ir.source_bullets
    }
    by_bullet: dict[str, list[str]] = {}
    for unit in units:
        linked = [
            bullets_by_id[str(ref.get("bullet_id") or "")]
            for ref in unit.source_refs
            if str(ref.get("bullet_id") or "") in bullets_by_id
        ]
        # Activity artifacts may come from an earlier attempt that only stored
        # bullet IDs. Rehydrate all derived fields from immutable SourceBullet
        # data during every assembly, never from model-authored text.
        if linked:
            unit.raw_text = "\n".join(item.raw_text for item in linked)
            unit.source_line_start = min(item.source_line_start for item in linked)
            unit.source_line_end = max(item.source_line_end for item in linked)
            unit.source_block_ids = list(dict.fromkeys(
                block_id for item in linked for block_id in item.source_block_ids
            ))
            unit.source_refs = [
                {"bullet_id": item.source_bullet_id, "quote": item.raw_text}
                for item in linked
            ]
        for ref in unit.source_refs:
            by_bullet.setdefault(str(ref["bullet_id"]), []).append(
                unit.work_unit_id
            )
    for bullet in resume_ir.source_bullets:
        bullet.work_unit_ids = list(
            dict.fromkeys(by_bullet.get(bullet.source_bullet_id, []))
        )


def _prompt_text() -> str:
    return (
        Path(__file__).with_name("prompts").joinpath("work_unit_extraction.md")
        .read_text(encoding="utf-8")
    )


def _schema() -> dict[str, Any]:
    return json.loads(
        Path(__file__).with_name("schemas").joinpath("work_unit_extraction.json")
        .read_text(encoding="utf-8")
    )
