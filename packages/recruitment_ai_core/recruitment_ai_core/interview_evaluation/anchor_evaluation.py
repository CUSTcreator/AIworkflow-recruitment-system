"""面评单元对冻结拓扑锚点的批量语义判断。

本模块只调用 LLM 产生判断矩阵，不修改分数，也不访问数据库。批次由上层按照
``llm_budget.pack_llm_batches`` 规划。单条非法结果会被丢弃，但有效行会保留，并且
只对缺失的 ``unit x anchor`` 配对做一次格式修复请求。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from recruitment_ai_core.llm import LLMResponseError, call_json_llm


logger = logging.getLogger(__name__)
_JUDGEMENTS = {
    "support",
    "verified",
    "partial",
    "not_support",
    "weak_contradiction",
    "contradicted",
    "strong_contradiction",
}
# response_format 与本地验证使用同一份最小合同；模型不能返回解释、分数或新 ID。
_SCHEMA = {
    "type": "object",
    "required": ["judgements"],
    "additionalProperties": False,
    "properties": {
        "judgements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["unitId", "anchorId", "judgement"],
                "additionalProperties": False,
                "properties": {
                    "unitId": {"type": "string"},
                    "anchorId": {"type": "string"},
                    "judgement": {"type": "string", "enum": sorted(_JUDGEMENTS)},
                },
            },
        }
    },
}
# 供应商支持时由上面的严格 Schema 直接约束输出；本地只先确认容器形状，再由
# ``_validated_rows`` 逐行做 ID、枚举和精确字段校验，以保留同批中的合法子集。
_LOCAL_VALIDATION_SCHEMA = {
    "type": "object",
    "required": ["judgements"],
    "additionalProperties": False,
    "properties": {
        "judgements": {"type": "array", "items": {"type": "object"}},
    },
}


def compact_anchor(node: Mapping[str, Any]) -> dict[str, Any]:
    """将拓扑节点投影为短语义输入；分数和完整上下文留给后端增量计算。"""
    refs = [
        dict(item)
        for item in (node.get("references") or ())
        if isinstance(item, Mapping)
    ]
    parts = [
        str(
            node.get("description") or node.get("label") or node.get("name") or ""
        ).strip()
    ]
    parts.extend(
        str(
            item.get("text") or item.get("description") or item.get("name") or ""
        ).strip()
        for item in refs
    )
    result = {
        "anchorId": str(node.get("nodeId") or node.get("anchorId") or ""),
        "anchorType": str(node.get("anchorType") or node.get("anchor_type") or ""),
        "description": "；".join(item for item in parts if item),
    }
    if not result["description"]:
        raise ValueError("post_interview_anchor_description_missing")
    return result


def _validated_rows(
    response: Any,
    *,
    unit_ids: set[str],
    anchor_ids: set[str],
) -> list[dict[str, str]]:
    if not isinstance(response, Mapping):
        raise LLMResponseError("post_interview_anchor_response_not_object")
    rows = response.get("judgements")
    if not isinstance(rows, list):
        raise LLMResponseError("post_interview_anchor_judgements_not_array")
    result: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "unitId",
            "anchorId",
            "judgement",
        }:
            continue
        unit_id = str(row.get("unitId") or "").strip()
        anchor_id = str(row.get("anchorId") or "").strip()
        judgement = str(row.get("judgement") or "").strip()
        pair = (unit_id, anchor_id)
        if (
            unit_id not in unit_ids
            or anchor_id not in anchor_ids
            or judgement not in _JUDGEMENTS
            or pair in seen_pairs
        ):
            continue
        seen_pairs.add(pair)
        result.append(
            {"unitId": unit_id, "anchorId": anchor_id, "judgement": judgement}
        )
    return result


def _call_anchor_model(
    *,
    units: list[dict[str, str]],
    anchors: list[dict[str, Any]],
    workflow_name: str,
    required_pairs: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    payload: dict[str, Any] = {"units": units, "anchors": anchors}
    if required_pairs is not None:
        payload["requiredPairs"] = [
            {"unitId": unit_id, "anchorId": anchor_id}
            for unit_id, anchor_id in required_pairs
        ]
    scope = (
        "只返回 requiredPairs 中每个配对的判断，每个配对恰好一行。"
        if required_pairs is not None
        else "返回 units 与 anchors 笛卡尔积中的每个配对，每个配对恰好一行。"
    )
    response, trace = call_json_llm(
        workflow_name=workflow_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "你只判断给定面评事实与冻结评分锚点的关系。"
                    "必须按当前 unit 相对当前 anchor 的方向逐对判断，不能按 unit 的整体印象判断。"
                    "每个配对必须先经过直接相关性门槛：只使用当前 unit.text 和当前 anchor.description，"
                    "判断 unit 是否直接评价 anchor 的核心知识、工具、行为、职责或结果。不得使用简历"
                    "其他事实、上一版分数、行业常识或相同专业背景补全 unit 没有表达的内容。"
                    "只属于相同专业、行业或项目背景，或者只能通过宽泛类别联想到锚点时，必须返回"
                    "not_support；没有通过直接相关性门槛时，不得继续判断正向、负向或强度。"
                    "通过门槛后再依次判断：只有待核实或存疑而没有负面事实时返回 not_support；"
                    "有肯定事实但只覆盖锚点部分含义时返回 partial；事实明确支持锚点核心含义时返回"
                    "support；有具体、完整且可核验的强支持时返回 verified。"
                    "负向判断必须直接指向当前锚点：局部不足、经验偏少或间接风险，只轻微削弱当前锚点时"
                    "返回 weak_contradiction；明确表示缺少、没有、未达到或能力不足，并直接否定当前"
                    "锚点核心含义时返回 contradicted；只有具体失败事实、明确结果或多项一致事实足以"
                    "证明当前锚点明显不成立时，才返回 strong_contradiction。"
                    "partial 只能表达正向但不完整的证据；not_support 不表示负面结论，也不产生扣分。"
                    "面评提到某类实习或岗位经验不足，并不能反证问题界定、方案执行或结果价值等其他"
                    "经历锚点；对没有直接语义关系的锚点必须返回 not_support。"
                    "边界示例：‘项目经历丰富，涉及结构设计和仿真’对‘熟悉CAD并绘制工程图纸’返回"
                    "not_support，因为原文没有说明CAD或工程图纸；‘使用SolidWorks完成三维建模’对该"
                    "复合锚点只返回 partial；‘独立使用CAD绘制并修改工程图纸’才可返回 support。"
                    "‘本科GPA较低，学术功底存疑’对‘机械结构设计能力’返回 not_support，只能对直接"
                    "评价理论或学术基础的锚点考虑 weak_contradiction。‘实习经历几乎空白’对‘具备"
                    "采购实习经验’可返回 contradicted，但对‘方案执行能力’必须返回 not_support。"
                    "例如 unit 为‘做过 Java 项目，但没有高并发经验’：对‘Java 开发能力’可判断为"
                    "partial 或 support；对‘高并发系统经验’根据负面事实的明确程度返回"
                    "weak_contradiction 或 contradicted；对其他无关能力返回 not_support。"
                    "不得创建 ID、计算分数或输出解释。只返回符合 JSON Schema 的对象。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{scope} judgement 只能是 support、verified、partial、"
                    "not_support、weak_contradiction、contradicted、strong_contradiction。"
                    "没有直接支持或反证关系也必须明确返回 not_support，不得省略。\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ],
        schema_name="interview_anchor_judgement_v2",
        json_schema=_SCHEMA,
        local_validation_schema=_LOCAL_VALIDATION_SCHEMA,
    )
    if not isinstance(response, Mapping):
        code = (
            "post_interview_anchor_model_unavailable"
            if str(trace.get("mode") or "") == "disabled"
            else "post_interview_anchor_response_not_object"
        )
        raise LLMResponseError(code)
    return _validated_rows(
        response,
        unit_ids={item["unitId"] for item in units},
        anchor_ids={str(item["anchorId"]) for item in anchors},
    )


def evaluate_anchor_batch(
    *,
    units: Sequence[Mapping[str, Any]],
    anchors: Sequence[Mapping[str, Any]],
    workflow_name: str,
) -> dict[str, Any]:
    """执行一个批次；保留合法行，并只补请求缺失配对一次。"""
    unit_payload = [
        {
            "unitId": str(item.get("unitId") or item.get("unit_id") or "").strip(),
            "text": str(item.get("text") or "").strip(),
        }
        for item in units
    ]
    anchor_payload = [compact_anchor(item) for item in anchors]
    unit_ids = [item["unitId"] for item in unit_payload]
    anchor_ids = [str(item["anchorId"]) for item in anchor_payload]
    if (
        any(not value for value in (*unit_ids, *anchor_ids))
        or len(set(unit_ids)) != len(unit_ids)
        or len(set(anchor_ids)) != len(anchor_ids)
    ):
        raise ValueError("post_interview_anchor_input_ids_invalid")
    expected_pairs = [
        (unit_id, anchor_id) for unit_id in unit_ids for anchor_id in anchor_ids
    ]
    if not expected_pairs:
        return {
            "judgements": [],
            "coverageComplete": True,
            "expectedPairCount": 0,
            "validPairCount": 0,
            "missingPairs": [],
            "repairAttempted": False,
        }

    try:
        result = _call_anchor_model(
            units=unit_payload,
            anchors=anchor_payload,
            workflow_name=workflow_name,
        )
    except LLMResponseError:
        # 空对象/非对象仍给模型一次最小修复机会；第二次失败必须向上抛出，不能伪装成空判断。
        result = []
    seen_pairs = {(item["unitId"], item["anchorId"]) for item in result}
    missing_pairs = [pair for pair in expected_pairs if pair not in seen_pairs]
    repair_attempted = bool(missing_pairs)
    if missing_pairs:
        missing_unit_ids = {pair[0] for pair in missing_pairs}
        missing_anchor_ids = {pair[1] for pair in missing_pairs}
        repaired = _call_anchor_model(
            units=[item for item in unit_payload if item["unitId"] in missing_unit_ids],
            anchors=[
                item
                for item in anchor_payload
                if str(item["anchorId"]) in missing_anchor_ids
            ],
            workflow_name=workflow_name,
            required_pairs=missing_pairs,
        )
        missing_set = set(missing_pairs)
        for item in repaired:
            pair = (item["unitId"], item["anchorId"])
            if pair in missing_set and pair not in seen_pairs:
                result.append(item)
                seen_pairs.add(pair)
    missing_pairs = [pair for pair in expected_pairs if pair not in seen_pairs]
    return {
        "judgements": result,
        "coverageComplete": not missing_pairs,
        "expectedPairCount": len(expected_pairs),
        "validPairCount": len(result),
        "missingPairs": [
            {"unitId": unit_id, "anchorId": anchor_id}
            for unit_id, anchor_id in missing_pairs
        ],
        "repairAttempted": repair_attempted,
    }
