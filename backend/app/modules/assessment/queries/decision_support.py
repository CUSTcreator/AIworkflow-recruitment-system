from __future__ import annotations

import hashlib
from typing import Any


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_OPEN_RISK_STATUSES = {"open", "unresolved", "contradicted"}
_CLOSED_STATUSES = {"verified", "resolved", "closed"}
_MAX_FOCUS_ITEMS = 5
_LEGACY_PLACEHOLDER_TEXTS = {
    "确认候选人岗位动机、实习周期和到岗条件。",
    "记录是否需要补充技术复核，但 HR 不直接关闭技术风险。",
    "一面后仍有技术风险未关闭，HR 需确认是否接受继续推进。",
    "请 HR 结合一面证据判断",
}
_RECOMMENDATION_LABELS = {
    "strong_recommend": "强烈推荐",
    "recommend": "推荐进入下一阶段",
    "hold": "暂缓决定",
    "not_recommend": "不建议继续推进",
}


def _screening_result(screening: dict[str, Any] | None) -> dict[str, Any]:
    value = screening or {}
    return value.get("screeningResultView") or value.get("screening_result_view") or value


def _text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _ids(item: dict[str, Any], *keys: str) -> list[str]:
    result: list[str] = []
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            result.extend(str(entry) for entry in value if entry)
    return list(dict.fromkeys(result))


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        text = str(value or "").strip(" \t\r\n；;，,")
        if text:
            result.append(text)
    return list(dict.fromkeys(result))


def _friendly_handoff_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text in _LEGACY_PLACEHOLDER_TEXTS:
        return ""
    if text in _RECOMMENDATION_LABELS:
        return _RECOMMENDATION_LABELS[text]
    internal_phrases = ("结构化证据", "审核包", "workflow", "Workflow")
    if any(phrase in text for phrase in internal_phrases):
        return ""
    return text


def _focus_item(
    item: dict[str, Any],
    *,
    index: int,
    source_kind: str,
) -> dict[str, Any]:
    priority = _text(item, "priority", "severity") or "medium"
    if priority not in _PRIORITY_ORDER:
        priority = "medium"
    title = _text(
        item, "title", "name", "remainingMissingInformation", "remaining_missing_information",
        "unknownPoint", "unknown_point", "reason", "summary",
    )
    conclusion = _text(item, "summary", "reason", "unknownPoint", "unknown_point")
    missing_information = _text(
        item,
        "remainingMissingInformation",
        "remaining_missing_information",
        "remaining_need",
    )
    action = _text(
        item,
        "expectedEvidence",
        "expected_evidence",
        "expected_result_type",
        "recommendedInterviewAction",
        "recommended_interview_action",
    )
    signal_id = _text(
        item, "id", "focusId", "interview_target_id", "targetId", "target_id"
    ) or f"{source_kind}_{index}"
    refs = _ids(
        item,
        "related_evidence_ids",
        "relatedEvidenceIds",
        "evidenceIds",
        "evidence_ids",
        "sourceEvidenceIds",
        "source_evidence_ids",
        "source_evidence_refs",
    )
    target_refs = _clean_list(
        [
            item.get("requirementId") or item.get("requirement_id"),
            item.get("riskId") or item.get("risk_id"),
            item.get("targetId") or item.get("target_id"),
        ]
    )
    return {
        "focusId": signal_id,
        "title": title,
        "priority": priority,
        "decisionImpact": "informative",
        "currentConclusion": conclusion,
        "verificationAction": action,
        "status": _text(item, "status") or "open",
        "sourceKinds": [source_kind],
        "sourceRefs": refs,
        "sourceSignalIds": [signal_id],
        "sourceTargetRefs": target_refs,
        "missingInformation": missing_information if source_kind == "interview_target" else "",
    }


def _normalized_title(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _merge_focus_items(
    items: list[dict[str, Any]],
    *,
    title: str = "",
    reason: str = "",
    action: str = "",
) -> dict[str, Any]:
    ordered = sorted(items, key=lambda item: (_PRIORITY_ORDER[item["priority"]], item["title"]))
    primary = ordered[0]
    signal_ids = list(dict.fromkeys(value for item in ordered for value in item["sourceSignalIds"]))
    priority = primary["priority"]
    kinds = list(dict.fromkeys(value for item in ordered for value in item["sourceKinds"]))
    status = "contradicted" if any(item["status"] == "contradicted" for item in ordered) else "open"
    focus_title = title.strip() or primary["title"]
    focus_id = "focus_" + hashlib.sha1("|".join(sorted(signal_ids)).encode("utf-8")).hexdigest()[:12]
    conclusions = list(dict.fromkeys(item["currentConclusion"] for item in ordered if item["currentConclusion"]))
    missing = list(dict.fromkeys(item["missingInformation"] for item in ordered if item["missingInformation"]))
    return {
        "focusId": focus_id,
        "title": focus_title,
        "priority": priority,
        "decisionImpact": "informative",
        "oneLineReason": reason.strip() or (conclusions[0] if conclusions else ""),
        "currentConclusion": "\uff1b".join(conclusions),
        "verificationAction": action.strip() or primary["verificationAction"],
        "status": status,
        "sourceKinds": kinds,
        "sourceRefs": list(dict.fromkeys(value for item in ordered for value in item["sourceRefs"])),
        "sourceSignalIds": signal_ids,
        "sourceTargetRefs": list(dict.fromkeys(value for item in ordered for value in item["sourceTargetRefs"])),
        "missingInformation": "\uff1b".join(missing),
    }


def _group_candidates(
    candidates: list[dict[str, Any]],
    decision_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    by_signal = {signal_id: item for item in candidates for signal_id in item["sourceSignalIds"]}
    used: set[str] = set()
    grouped: list[dict[str, Any]] = []
    for group in (decision_summary or {}).get("decisionFocusGroups") or []:
        if not isinstance(group, dict):
            continue
        signal_ids = _ids(group, "signalKeys", "signal_keys")
        members = [by_signal[signal_id] for signal_id in signal_ids if signal_id in by_signal and signal_id not in used]
        if not members:
            continue
        used.update(signal_id for item in members for signal_id in item["sourceSignalIds"])
        grouped.append(
            _merge_focus_items(
                members,
                title=_text(group, "title"),
                reason=_text(group, "reason"),
                action=_text(group, "verificationAction", "verification_action"),
            )
        )

    fallback_groups: list[list[dict[str, Any]]] = []
    for item in candidates:
        if any(signal_id in used for signal_id in item["sourceSignalIds"]):
            continue
        title_key = _normalized_title(item["title"])
        target_refs = set(item["sourceTargetRefs"])
        matching_indexes = [
            index
            for index, bucket in enumerate(fallback_groups)
            if any(_normalized_title(member["title"]) == title_key for member in bucket)
            or bool(target_refs.intersection(ref for member in bucket for ref in member["sourceTargetRefs"]))
        ]
        if not matching_indexes:
            fallback_groups.append([item])
            continue
        primary_index = matching_indexes[0]
        fallback_groups[primary_index].append(item)
        for index in reversed(matching_indexes[1:]):
            fallback_groups[primary_index].extend(fallback_groups.pop(index))
    grouped.extend(_merge_focus_items(items) for items in fallback_groups)
    return grouped


def build_decision_focus(
    screening: dict[str, Any] | None,
    *,
    plan: dict[str, Any] | None = None,
    decision_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _screening_result(screening)
    raw = result.get("interviewFocus") or {}
    targets = list(raw.get("firstInterviewFocus") or [])
    if plan and plan.get("targets"):
        targets = list(plan["targets"])
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(targets, start=1):
        candidates.append(_focus_item(item, index=index, source_kind="interview_target"))

    candidates = [
        item for item in candidates
        if item.get("status") not in _CLOSED_STATUSES and item.get("title")
    ]
    ranked = sorted(
        _group_candidates(candidates, decision_summary),
        key=lambda item: (
            _PRIORITY_ORDER[item["priority"]],
            0 if item["status"] == "contradicted" else 1,
            0 if item["decisionImpact"] == "blocking" else 1,
            -len(item["sourceSignalIds"]),
            item["title"],
        ),
    )
    returned = ranked[:_MAX_FOCUS_ITEMS]
    public_fields = (
        "focusId",
        "title",
        "priority",
        "oneLineReason",
        "currentConclusion",
        "verificationAction",
        "status",
        "sourceRefs",
        "missingInformation",
    )
    return {
        "items": [
            {field: item[field] for field in public_fields}
            for item in returned
        ]
    }

def build_assessment_coverage(
    plan: dict[str, Any] | None,
    progress: dict[str, Any] | None,
) -> dict[str, Any]:
    plan = plan or {}
    progress = progress or {}
    targets = [item for item in plan.get("targets", []) if item.get("selected", True)]
    target_ids = {str(item.get("targetId")) for item in targets if item.get("targetId")}
    question_targets = {
        str(question.get("questionId")): {
            str(target_id) for target_id in question.get("verificationTargetIds", []) if target_id
        }
        for question in plan.get("questions", [])
    }
    assessed_target_ids: set[str] = set()
    partial_target_ids: set[str] = set()
    for response in progress.get("questionResponses", []) or []:
        question_id = str(response.get("questionId") or "")
        has_record = bool(
            str(response.get("answerSummary") or "").strip()
            or str(response.get("interviewerNote") or "").strip()
        )
        if not has_record:
            continue
        linked = question_targets.get(question_id, set())
        # 覆盖率表示面试官是否实际记录了该目标，不再混入已由后续算法独立判断的支持程度。
        assessed_target_ids.update(linked)

    statuses = progress.get("verificationTargetStatuses") or {}
    for target_id, status in statuses.items():
        if status == "verified":
            assessed_target_ids.add(str(target_id))
        elif status == "partially_verified":
            partial_target_ids.add(str(target_id))

    assessed_target_ids &= target_ids
    partial_target_ids = (partial_target_ids & target_ids) - assessed_target_ids
    unassessed_target_ids = target_ids - assessed_target_ids - partial_target_ids
    total = len(target_ids)
    completed = len(assessed_target_ids)
    partial = len(partial_target_ids)
    return {
        "planned": total,
        "fullyAssessed": completed,
        "partiallyAssessed": partial,
        "notAssessed": len(unassessed_target_ids),
        "coverageRate": round((completed + 0.5 * partial) / total, 4) if total else 0.0,
    }


def build_stage_handoff(
    *,
    first_assessment: dict[str, Any] | None = None,
    review_package: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    first_assessment = first_assessment or {}
    review_package = review_package or {}
    if not first_assessment and not review_package:
        return None
    recommendation = _text(first_assessment, "overallRecommendation") or _text(
        review_package, "recommendation"
    )
    decision_reason = _text(first_assessment, "decisionReason", "summary")
    return {
        "recommendation": _friendly_handoff_text(recommendation),
        "decisionReason": _friendly_handoff_text(decision_reason),
        "confirmedStrengths": _clean_list(review_package.get("confirmed_strengths")),
        "remainingItems": _clean_list(review_package.get("unresolved_weaknesses")),
    }

def build_hr_conditions(
    final_package: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, value in enumerate((final_package or {}).get("recommended_conditions") or [], start=1):
        items.append(
            {
                "conditionId": f"recommended_{index}",
                "label": "推进条件",
                "status": "concern",
                "value": str(value),
            }
        )
    return items


def build_decision_support(
    screening: dict[str, Any] | None,
    *,
    plan: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    first_assessment: dict[str, Any] | None = None,
    review_package: dict[str, Any] | None = None,
    final_package: dict[str, Any] | None = None,
    decision_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "decision_support_v2_0",
        "decisionFocus": build_decision_focus(
            screening,
            plan=plan,
            decision_summary=decision_summary,
        ),
        "assessmentCoverage": build_assessment_coverage(plan, progress),
        "stageHandoff": build_stage_handoff(
            first_assessment=first_assessment,
            review_package=review_package,
        ),
        "hrConditions": build_hr_conditions(final_package),
    }
