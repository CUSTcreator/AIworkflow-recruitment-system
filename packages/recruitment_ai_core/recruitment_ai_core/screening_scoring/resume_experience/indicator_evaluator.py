from __future__ import annotations

from typing import Any

from recruitment_ai_core.execution import map_bounded
from recruitment_ai_core.llm_budget import LlmBudgetPolicy, estimate_tokens, pack_llm_batches

from .contracts import LEVEL_STRENGTH
from .llm_gateway import ResumeExperienceLLMGateway


def evaluate_indicators(
    work_units: list[dict[str, Any]],
    gateway: ResumeExperienceLLMGateway,
    project_id: str,
    project_context: list[dict[str, Any]] | None = None,
    *,
    preset_model: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], bool, list[str]]:
    # 1. 固定本次允许判定的指标目录，模型不能产生目录之外的能力标签。
    indicator_specs = list(preset_model["indicators"])
    indicator_ids = {item["indicator_id"] for item in indicator_specs}
    model_ref = {
        "preset_model_id": preset_model["model_id"],
        "preset_model_version": preset_model["version"],
    }
    # 2. 将项目工作单元按批切分，控制单次模型输入长度并保持每个单元独立可追溯。
    batches = [
        list(batch.items)
        for batch in pack_llm_batches(
            work_units,
            prompt_tokens=900,
            schema_tokens=700,
            policy=LlmBudgetPolicy(max_items=gateway.indicator_batch_size),
            estimate_item_input=lambda item: estimate_tokens(item),
            estimate_item_output=lambda _item: 220,
        )
    ]
    # 3. 对每批工作单元并发判定“哪些指标被激活、等级是多少”。
    #    WorkUnit 本身就是已冻结的最小候选证据，模型不得另行选择或抄写证据。
    batch_results = map_bounded(
        "resume_work_unit_indicator_batch", batches,
        lambda batch: _evaluate_unit_batch(
            batch, gateway, model_ref, indicator_specs, indicator_ids
        ),
        max_concurrency=gateway.max_parallel_batches,
    )
    # 4. 仅校验模型返回的指标和等级；来源由后端的 WorkUnit 固定，不参与模型输出。
    unit_results, degraded, errors = [], False, []
    for batch, response in batch_results:
        if response is None:
            response = {
                "evaluations": [
                    {"target_id": item["work_unit_id"], "activated_indicators": []}
                    for item in batch
                ]
            }
            degraded = True
        normalized, item_errors = _normalize_unit_results(
            response["evaluations"], batch, indicator_ids
        )
        unit_results.extend(normalized)
        errors.extend(item_errors)
        degraded = degraded or bool(item_errors)

    # 5. 基于已通过校验的单元结果再做项目层归纳，提取问题—方法—结果与跨单元指标。
    project_response = gateway.call(
        "project_indicator",
        {
            **model_ref,
            "project_id": project_id,
            "project_context": project_context or [],
            "work_units": work_units,
            "work_unit_indicator_results": unit_results,
            "indicators": indicator_specs,
        },
        lambda value: _project_response(
            value, project_id, work_units, indicator_ids
        ),
    )
    # 6. 模型不可用时生成空项目结论并标记降级，避免虚构证据或阻断整条流程。
    if project_response is None:
        project_response = {
            "project_id": project_id,
            "project_pao": {
                "problem": None,
                "approach": None,
                "outcome": None,
            },
            "activated_indicators": [],
        }
        degraded = True
    # 7. 验证项目层问题、方法、结果是否能回链到该项目工作单元。
    project_pao, pao_errors = _normalize_project_pao(
        project_response["project_pao"], project_id, work_units
    )
    errors.extend(pao_errors)
    degraded = degraded or bool(pao_errors)
    # 8. 验证跨单元指标的目录和等级；它的输入范围固定为本项目全部 WorkUnit。
    project_results, item_errors = _normalize_project_results(
        project_response, work_units, indicator_ids
    )
    errors.extend(item_errors)
    degraded = degraded or bool(item_errors)
    # 9. 返回单元级结果、项目级结果、降级状态和校验错误，交由聚合器计算经历能力。
    return (
        unit_results,
        project_pao,
        project_results,
        degraded,
        errors,
    )


def _evaluate_unit_batch(
    batch: list[dict[str, Any]],
    gateway: ResumeExperienceLLMGateway,
    model_ref: dict[str, str],
    indicator_specs: list[dict[str, Any]],
    indicator_ids: set[str],
):
    allowed = {item["work_unit_id"] for item in batch}
    response = gateway.call(
        "work_unit_indicator",
        {
            **model_ref,
            "work_units": batch,
            "allowed_target_ids": sorted(allowed),
            "indicators": indicator_specs,
        },
        lambda value: _evaluation_response(value, allowed, indicator_ids),
    )
    return batch, response


def _normalize_unit_results(
    evaluations: list[dict[str, Any]],
    work_units: list[dict[str, Any]],
    indicator_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    units = {item["work_unit_id"]: item for item in work_units}
    output, errors = [], []
    seen_targets: set[str] = set()
    for evaluation in evaluations:
        if not isinstance(evaluation, dict):
            errors.append("work_unit_evaluation:invalid_item")
            continue
        target_id = evaluation.get("target_id")
        unit = units.get(target_id)
        if not unit:
            errors.append("work_unit_evaluation:unknown_target")
            continue
        if target_id in seen_targets:
            errors.append(f"work_unit_evaluation:{target_id}:duplicate")
            continue
        seen_targets.add(target_id)
        results = evaluation.get("activated_indicators", [])
        if not isinstance(results, list):
            errors.append(f"work_unit_evaluation:{target_id}:invalid_indicators")
            continue
        seen_indicators: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                errors.append(f"work_unit_evaluation:{target_id}:invalid_item")
                continue
            indicator_id, level = result.get("indicator_id"), result.get("level")
            normalized_level = _normalize_level(level)
            # 部分模型会用 0/null 表示“未激活”。该语义无歧义，按空结果处理，
            # 不能把模型的占位习惯误记成候选人数据质量异常。
            if normalized_level is None:
                continue
            if not isinstance(indicator_id, str) or indicator_id not in indicator_ids:
                errors.append(f"work_unit_evaluation:{target_id}:unknown_indicator")
                continue
            if normalized_level is _INVALID_LEVEL:
                errors.append(f"work_unit_evaluation:{target_id}:invalid_level")
                continue
            if indicator_id in seen_indicators:
                errors.append(f"work_unit_evaluation:{target_id}:duplicate_indicator")
                continue
            reason = result.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"work_unit_evaluation:{target_id}:invalid_reason")
                continue
            seen_indicators.add(indicator_id)
            output.append({
                "work_unit_indicator_result_id": (
                    f"WUIR_{unit['work_unit_id']}_{indicator_id}"
                ),
                "project_id": unit["project_id"],
                "work_unit_id": unit["work_unit_id"],
                "indicator_id": indicator_id,
                "level": normalized_level,
                "score": LEVEL_STRENGTH[normalized_level],
                "reason": reason.strip(),
            })
    errors.extend(
        f"work_unit_evaluation:{target_id}:missing"
        for target_id in sorted(set(units) - seen_targets)
    )
    return output, errors


def _normalize_project_results(
    payload: dict[str, Any],
    work_units: list[dict[str, Any]],
    indicator_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    units = {item["work_unit_id"]: item for item in work_units}
    output, errors, seen = [], [], set()
    for result in payload.get("activated_indicators", []):
        if not isinstance(result, dict):
            errors.append("project_indicator:invalid_item")
            continue
        indicator_id, level = result.get("indicator_id"), result.get("level")
        normalized_level = _normalize_level(level)
        if normalized_level is None:
            continue
        if not isinstance(indicator_id, str) or indicator_id not in indicator_ids:
            errors.append("project_indicator:unknown_indicator")
            continue
        if normalized_level is _INVALID_LEVEL:
            errors.append("project_indicator:invalid_level")
            continue
        if indicator_id in seen:
            errors.append("project_indicator:duplicate_indicator")
            continue
        reason = result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("project_indicator:invalid_reason")
            continue

        seen.add(indicator_id)
        output.append({
            "target_type": "project",
            "target_id": payload["project_id"],
            "project_id": payload["project_id"],
            "indicator_id": indicator_id,
            "level": normalized_level,
            "score": LEVEL_STRENGTH[normalized_level],
            "reason": reason.strip(),
            # 项目级指标的可用输入范围在后端固定为整个项目；不让模型反向决定来源。
            "work_unit_ids": sorted(units),
        })
    return output, errors


def _normalize_project_pao(
    payload: dict[str, Any],
    project_id: str,
    work_units: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """只接受模型返回的 WorkUnit ID，并由后端校验其项目归属。"""
    units = {item["work_unit_id"]: item for item in work_units}
    output = {
        "project_pao_result_id": f"PPAO_{project_id}",
        "project_id": project_id,
    }
    errors: list[str] = []
    for key in ("problem", "approach", "outcome"):
        if key not in payload:
            errors.append(f"project_pao:{key}:missing")
            output[key] = None
            continue
        part = payload[key]
        if part is None:
            output[key] = None
            continue
        if not isinstance(part, dict):
            errors.append(f"project_pao:{key}:invalid_reference")
            output[key] = None
            continue
        summary = part.get("summary")
        raw_ids = part.get("work_unit_ids")
        raw_evidence_ids = part.get("evidence_work_unit_ids")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(raw_ids, list)
            or not isinstance(raw_evidence_ids, list)
            or not all(isinstance(value, str) for value in raw_ids)
            or not all(isinstance(value, str) for value in raw_evidence_ids)
        ):
            errors.append(f"project_pao:{key}:invalid_reference")
            output[key] = None
            continue
        ids = list(dict.fromkeys(value for value in raw_ids if value))
        evidence_ids = list(dict.fromkeys(value for value in raw_evidence_ids if value))
        valid = (
            bool(summary.strip())
            and bool(ids)
            and set(ids) <= set(units)
            and set(evidence_ids) <= set(ids)
        )
        if not valid:
            errors.append(f"project_pao:{key}:invalid_reference")
            output[key] = None
            continue
        output[key] = {
            "summary": summary.strip(),
            "work_unit_ids": ids,
            "evidence_work_unit_ids": evidence_ids,
        }
    return output, errors

def _evaluation_response(
    value: dict[str, Any],
    target_ids: set[str],
    indicator_ids: set[str] | None = None,
) -> tuple[bool, str]:
    rows = value.get("evaluations")
    if not isinstance(rows, list):
        return False, "evaluations_missing"
    if target_ids and not any(
        isinstance(item, dict)
        and item.get("target_id") in target_ids
        and isinstance(item.get("activated_indicators"), list)
        for item in rows
    ):
        # 只有整批没有任何可恢复的目标结果时才让网关做一次修复调用。
        return False, "no_usable_target_evaluation"
    # 具体条目由归一化阶段逐项过滤；一个坏 WorkUnit 不应拖垮同批正常项。
    return True, ""


def _project_response(
    value: dict[str, Any],
    project_id: str,
    work_units: list[dict[str, Any]],
    indicator_ids: set[str],
) -> tuple[bool, str]:
    if value.get("project_id") != project_id or not isinstance(value.get("project_pao"), dict):
        return False, "invalid_project"
    project_pao = value["project_pao"]
    if not isinstance(value.get("activated_indicators"), list):
        return False, "activated_indicators_missing"
    return True, ""


_INVALID_LEVEL = object()


def _normalize_level(value: Any) -> int | None | object:
    """归一化无歧义等级；0/null 代表未激活，其余非法值交给业务过滤。"""
    if value is None or value == 0 or value == "0" or value == "":
        return None
    if isinstance(value, bool):
        return _INVALID_LEVEL
    if isinstance(value, int) and 1 <= value <= 5:
        return value
    if isinstance(value, str) and value in {"1", "2", "3", "4", "5"}:
        return int(value)
    return _INVALID_LEVEL
