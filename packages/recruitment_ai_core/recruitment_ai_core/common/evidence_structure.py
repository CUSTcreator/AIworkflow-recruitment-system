from __future__ import annotations

from typing import Any


HIGH_ORDER_FEATURES = {
    "named_constraint",
    "alternative_or_tradeoff",
    "failure_condition",
    "baseline_or_comparison",
    "measurement_scope",
    "external_or_production_use",
}


def evidence_roles(evidence: dict[str, Any]) -> set[str]:
    return {
        str(role)
        for clause in evidence.get("evidence_clauses", [])
        if isinstance(clause, dict)
        for role in clause.get("roles", [])
    }


def evidence_features(evidence: dict[str, Any]) -> set[str]:
    return {
        str(feature)
        for clause in evidence.get("evidence_clauses", [])
        if isinstance(clause, dict)
        for feature in clause.get("features", [])
    }


def generic_evidence_cap(evidence: dict[str, Any]) -> int:
    roles = evidence_roles(evidence)
    features = evidence_features(evidence)
    has_action = bool(evidence.get("action") and evidence.get("object"))
    has_result = bool(evidence.get("result") or evidence.get("metrics") or "outcome_validation" in roles)
    if not has_action:
        return 1 if roles - {"generic_claim"} else 0
    cap = 2
    if "explicit_mechanism" in features or "reasoning_decision" in roles:
        cap = 3
    if features & HIGH_ORDER_FEATURES:
        cap = max(cap, 4)
    if (
        "external_or_production_use" in features
        and has_result
        and len(features & HIGH_ORDER_FEATURES) >= 2
        and "explicit_mechanism" in features
    ):
        cap = 5
    return cap


def resume_indicator_cap(evidence: dict[str, Any], indicator_id: str) -> int:
    roles = evidence_roles(evidence)
    features = evidence_features(evidence)
    generic = generic_evidence_cap(evidence)
    has_action = bool(evidence.get("action") and evidence.get("object"))
    has_result = bool(evidence.get("result") or evidence.get("metrics") or "outcome_validation" in roles)

    if indicator_id == "task_problem_clarity":
        if not has_action:
            return 1
        cap = 2
        if "problem_context" in roles:
            cap = 3
        if "named_constraint" in features:
            cap = 4
        if "reasoning_decision" in roles and "alternative_or_tradeoff" in features:
            cap = 5
        return cap

    if indicator_id == "context_value_constraints":
        if not ({"problem_context", "constraint_boundary"} & roles or "external_or_production_use" in features):
            return 1
        cap = 2
        if "named_constraint" in features or "external_or_production_use" in features:
            cap = 3
        if len(features & {"named_constraint", "alternative_or_tradeoff", "failure_condition"}) >= 2:
            cap = 4
        if "alternative_or_tradeoff" in features and "external_or_production_use" in features:
            cap = 5
        return cap

    if indicator_id == "analysis_diagnosis":
        if "reasoning_decision" not in roles:
            return 1
        cap = 2
        if "explicit_mechanism" in features:
            cap = 3
        if "failure_condition" in features or "alternative_or_tradeoff" in features:
            cap = 4
        if len(features & {"failure_condition", "alternative_or_tradeoff", "measurement_scope"}) >= 2:
            cap = 5
        return cap

    if indicator_id == "decision_optimization_basis":
        if "reasoning_decision" not in roles:
            return 2 if has_action and evidence.get("methods") else 1
        cap = 2
        if "explicit_mechanism" in features or "named_constraint" in features:
            cap = 3
        if "alternative_or_tradeoff" in features:
            cap = 4
        if "alternative_or_tradeoff" in features and has_result and (
            "measurement_scope" in features or "external_or_production_use" in features
        ):
            cap = 5
        return cap

    if indicator_id == "result_evidence":
        if not has_result:
            return 1
        if "quantified_result" not in features:
            return 2
        cap = 3
        if "baseline_or_comparison" in features or "measurement_scope" in features:
            cap = 4
        if (
            "baseline_or_comparison" in features
            and "measurement_scope" in features
            and "external_or_production_use" in features
        ):
            cap = 5
        return cap

    if indicator_id == "evaluation_iteration":
        if "outcome_validation" not in roles:
            return min(generic, 2)
        cap = 2
        if "explicit_mechanism" in features or "quantified_result" in features:
            cap = 3
        if "measurement_scope" in features or "failure_condition" in features:
            cap = 4
        if "measurement_scope" in features and "external_or_production_use" in features:
            cap = 5
        return cap

    return generic


def job_evidence_cap(evidence: dict[str, Any]) -> int:
    if "evidence_clauses" not in evidence:
        return 5
    return generic_evidence_cap(evidence)
