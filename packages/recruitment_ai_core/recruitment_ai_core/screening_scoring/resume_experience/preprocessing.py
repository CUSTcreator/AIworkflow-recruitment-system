from __future__ import annotations

from typing import Any

from .contracts import ResumeExperienceError
from .llm_gateway import ResumeExperienceLLMGateway


def prepare_project(
    project: dict[str, Any],
    gateway: ResumeExperienceLLMGateway,
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    # 1. 本步骤只整理上游已经结构化的工作单元，不再调用模型重新切分简历。
    del gateway
    project_id = str(
        project.get("experience_unit_id") or project.get("project_id")
    )
    # 2. 优先使用结构化阶段生成的工作单元；兼容旧数据时才从原文要点回退读取。
    rows = list(project.get("work_units") or [])
    # 4. 标题、背景说明或跨页残片可以没有工作事实。保留其结构化结果，
    #    由上层跳过本项目评分，绝不能因此中断整份简历的初筛。
    if not rows:
        return [], True, [f"scoring_skipped_no_work_units:{project_id}"]
    # 5. 统一工作单元 ID、项目归属和原文引用，并剔除没有原文的无效单元。
    units, legacy = _normalize_prestructured_units(project_id, rows)
    if not units:
        return [], True, [f"scoring_skipped_invalid_work_units:{project_id}"]
    return units, legacy, ["legacy_work_unit_source_ref"] if legacy else []


def prepare_existing_work_units(
    project_id: str,
    work_units: list[dict[str, Any]],
    gateway: ResumeExperienceLLMGateway,
    degraded: bool = False,
    project_name: str = "项目",
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    # 1. 本步骤只整理上游已经结构化的工作单元，不再调用模型重新切分简历。
    del gateway, project_name
    units, legacy = _normalize_prestructured_units(project_id, work_units)
    return (
        units,
        degraded or legacy,
        ["legacy_work_unit_source_ref"] if legacy else [],
    )


def _normalize_prestructured_units(
    project_id: str,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    output: list[dict[str, Any]] = []
    legacy = False
    for index, unit in enumerate(rows, start=1):
        refs = [
            {
                "bullet_id": str(
                    ref.get("bullet_id")
                    or ref.get("source_bullet_id")
                    or ""
                ),
                "quote": str(ref.get("quote") or "").strip(),
            }
            for ref in unit.get("source_refs", [])
            if str(ref.get("quote") or "").strip()
        ]
        raw_text = "；".join(ref["quote"] for ref in refs)
        if not raw_text or not refs:
            continue
        output.append(
            {
                "work_unit_id": str(
                    unit.get("work_unit_id")
                    or f"{project_id}_WU_{index:03d}"
                ),
                "project_id": project_id,
                "source_refs": refs,
                "raw_text": raw_text,
            }
        )
    return _deduplicate(output), legacy


def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        key = tuple(
            (str(ref["bullet_id"]), str(ref["quote"]))
            for ref in item["source_refs"]
        )
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output
