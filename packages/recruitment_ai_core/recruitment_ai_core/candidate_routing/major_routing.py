from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from recruitment_ai_core.llm import call_json_llm


class MajorRoutingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobMajorRequirement:
    job_id: str
    job_version_id: str
    title: str
    major_requirement: str


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["job_id", "relation", "basis"],
                "properties": {
                    "job_id": {"type": "string"},
                    "relation": {
                        "enum": ["exact", "related", "unrelated", "insufficient"]
                    },
                    "basis": {"type": "string", "minLength": 1, "maxLength": 450},
                },
            },
        }
    },
}


def manual_selection_decisions(
    *, jobs: list[JobMajorRequirement], reason: str
) -> list[dict[str, Any]]:
    """Keep a complete routing matrix when professional matching is unavailable.

    A job without a major requirement remains deterministically routable. Every
    constrained job stays in the result as an explicit manual-selection item so
    downstream publishing never silently drops it from the operator's choices.
    """
    decisions: list[dict[str, Any]] = []
    for item in jobs:
        if not item.major_requirement.strip():
            decisions.append({
                "job_id": item.job_id,
                "matched": True,
                "reason": "岗位未设置专业要求",
            })
            continue
        decisions.append({
            "job_id": item.job_id,
            "matched": False,
            "reason": reason,
            # ``matched=False`` can mean either a reliable mismatch or a missing
            # decision. This flag preserves that distinction for publication.
            "requires_manual_selection": True,
        })
    return sorted(decisions, key=lambda item: item["job_id"])


def route_candidate_major(
    *,
    candidate_major: str,
    source_quote: str,
    jobs: list[JobMajorRequirement],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return one validated boolean routing decision for every supplied job.

    模型只判断单一的专业关系；matched 与展示理由由后端派生，避免模型同时
    返回布尔值和自然语言理由时产生互相矛盾的两个结论。
    """
    normalized_major = candidate_major.strip()
    unrestricted = [item for item in jobs if not item.major_requirement.strip()]
    constrained = [item for item in jobs if item.major_requirement.strip()]
    decisions = [
        {"job_id": item.job_id, "matched": True, "reason": "岗位未设置专业要求"}
        for item in unrestricted
    ]
    trace: dict[str, Any] = {"mode": "no_constrained_jobs", "llm_trace": None}
    if not normalized_major:
        # 专业缺失不能挡住无约束岗位，也不能让有约束岗位从结果矩阵中消失。
        return manual_selection_decisions(
            jobs=jobs,
            reason="候选人专业信息不足，需人工选择岗位",
        ), {
            **trace,
            "mode": "candidate_major_missing",
            "warning": "candidate_major_missing",
        }
    if constrained:
        payload, trace = call_json_llm(
            workflow_name="candidate_major_routing",
            schema_name="candidate_major_routing_v2",
            json_schema=_SCHEMA,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "根据候选人明确写出的专业与岗位专业要求判断是否匹配。"
                        "只根据candidate_major、source_quote和major_requirement判断，"
                        "不得使用岗位名称、岗位职责或常识补写候选人事实。"
                        "必须为输入中的每个job_id返回且只返回一项，不得遗漏、重复或新增岗位。"
                        "relation只能是exact、related、unrelated或insufficient："
                        "候选人专业被岗位要求明确列举，或仅是同一专业的规范名称、全称或常用简称差异时为exact；"
                        "候选人专业未被直接列举，且岗位要求明确允许相关、相近、理工或工科等更宽专业范围时才为related；"
                        "岗位要求未明确允许相关或更宽专业范围时，即使专业看似相近也为unrelated；"
                        "候选人专业、原文引用或岗位专业要求不足以形成可靠判断时为insufficient。"
                        "basis必须是1到450字的简短判断依据，只能使用输入中的候选人专业、"
                        "原文引用和岗位要求，不得补写简历事实。只输出Schema规定的字段。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "candidate_major": normalized_major,
                            "source_quote": source_quote,
                            "jobs": [
                                {
                                    "job_id": item.job_id,
                                    "major_requirement": item.major_requirement,
                                }
                                for item in constrained
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        if payload is None:
            # LLM 不可用时保留规则可判定的岗位，同时为每个受约束岗位留下
            # 人工选择项，不能仅因已经有部分自动结果就静默丢弃其余岗位。
            return manual_selection_decisions(
                jobs=jobs,
                reason="自动专业匹配暂不可用，需人工选择岗位",
            ), {
                **trace,
                "mode": "candidate_major_routing_llm_unavailable",
                "warning": "candidate_major_routing_llm_unavailable",
            }
        llm_results = list(payload.get("results") or [])
        expected = {item.job_id for item in constrained}
        actual = [str(item.get("job_id") or "") for item in llm_results if isinstance(item, dict)]
        if len(actual) != len(expected) or set(actual) != expected or len(actual) != len(set(actual)):
            raise MajorRoutingError("candidate_major_routing_result_invalid")
        allowed_relations = {"exact", "related", "unrelated", "insufficient"}
        for item in llm_results:
            if (
                str(item.get("relation") or "") not in allowed_relations
                or not str(item.get("basis") or "").strip()
            ):
                raise MajorRoutingError("candidate_major_routing_result_invalid")
        for item in llm_results:
            relation = str(item["relation"])
            basis = str(item["basis"]).strip()[:450]
            matched = relation in {"exact", "related"}
            prefix = {
                "exact": "专业匹配",
                "related": "专业匹配",
                "unrelated": "专业不匹配",
                "insufficient": "专业信息不足",
            }[relation]
            decisions.append({
                "job_id": str(item["job_id"]),
                "matched": matched,
                "reason": f"{prefix}：{basis}"[:500],
            })
    return sorted(decisions, key=lambda item: item["job_id"]), trace
