from __future__ import annotations

"""Classify frozen job requirements before capability extraction.

Deterministic rules own explicit, low-ambiguity clauses. One optional LLM call
classifies only the remaining clauses. Invalid model items are isolated and the
caller can publish the deterministic result as a degraded, user-reviewable draft.
"""

import hashlib
import json
import re
from typing import Any

from recruitment_ai_core.llm import call_json_llm
from recruitment_ai_core.llm.errors import LLMResponseError


WORKFLOW_NAME = "job_capability"
SCHEMA_NAME = "job_requirement_classification_v1"
CATEGORIES = {
    "hard_screen",
    "required_capability",
    "preferred_capability",
    "non_scoring",
}
CRITERION_CODES = {
    "minimum_degree",
    "minimum_experience_years",
    "project_experience",
    "required_skill",
    "certification",
    "custom",
}

_PREFERRED = re.compile(r"优先|加分|更佳|较佳|最好|优先考虑|有则优先")
_NON_SCORING = re.compile(
    r"身体健康|职业道德|品行端正|责任心|抗压|性格|工作踏实|严谨务实|团队协作精神"
)
_CAPABILITY = re.compile(
    r"开发|设计|分析|实现|测试|优化|维护|搭建|构建|负责|参与|建设|交付|"
    r"熟悉|掌握|具备|经验|能力|编程|技术|系统|接口|沟通|协调|组织|执行|"
    r"研究|策划|管理|编制|使用|操作|了解"
)
_CERTIFICATION = re.compile(
    r"CET[-－]?\s*[46]|英语[四六六四]级|证书|资格证|注册[^，。；;]{0,20}(?:工程师|师)|"
    r"通过[^，。；;]{0,30}(?:考试|认证|培训)|持有|取得.*(?:证|资质)"
)
_EXPLICIT_REQUIRED = re.compile(r"必须|须|要求|至少|及以上|通过|持有|取得|需具备|应具备")
_MAJOR = re.compile(r"相关专业|所学专业|专业要求|专业方向|及其相关专业|等专业")
_CAPABILITY_ACTION = re.compile(
    r"熟悉|掌握|能够|能独立|善于使用|"
    r"(?:具备|具有|拥有)[^，。；;]{0,20}(?:能力|经验)|"
    r"^(?:负责|参与|承担|完成|使用|开发|编制|操作)"
)
_DEGREE = re.compile(r"博士|硕士|研究生|本科|学士|大专|专科")
_YEARS = re.compile(r"(?P<years>\d+(?:\.\d+)?)\s*年(?:及以上|以上|起)?(?:相关)?(?:工作|项目|行业)?经验")
_CUSTOM_HARD = re.compile(r"年龄|工作地点|户籍|国籍")


def classify_job_requirements(
    frozen_job_json: dict[str, Any],
    *,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return source-grounded classifications and hard-screening suggestions."""
    sources = prepare_requirement_sources(frozen_job_json)
    classified: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for source in sources:
        item = _classify_deterministic(source, frozen_job_json)
        (classified if item is not None else pending).append(item or source)

    traces: list[dict[str, Any]] = []
    if pending:
        payload, trace = call_json_llm(
            workflow_name=WORKFLOW_NAME,
            schema_name=SCHEMA_NAME,
            settings_overrides=_json_object_call_config(llm_config or {}),
            json_schema=_response_schema(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "只分类输入中的岗位任职要求，不补写原文没有的要求。"
                        "source_id必须逐项复制输入且恰好返回一次；source_quote必须完整复制source_text。"
                        "明确会淘汰不满足者的学历、年限、证书或其他强制条件才是hard_screen；"
                        "优先、加分、更佳不能是hard_screen。技术、经验和工作行为分别归入"
                        "required_capability或preferred_capability；健康、品行和泛化性格归入non_scoring。"
                        "hard_screen必须从允许的criterion_code中选择并返回可直接配置的expected_value；"
                        "其他分类的criterion_code和expected_value必须为null。只返回Schema字段。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "job_title": str(frozen_job_json.get("title") or ""),
                            "requirements": [
                                {
                                    "source_id": item["source_id"],
                                    "source_text": item["source_quote"],
                                }
                                for item in pending
                            ],
                            "allowed_criterion_codes": sorted(CRITERION_CODES),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        traces.append({**trace, "stage": "job_requirement_classification"})
        classified.extend(_validate_model_result(payload, pending))

    classified.sort(key=lambda item: (item["source_index"], item["atom_index"]))
    return {
        "items": classified,
        "hardScreeningRules": build_hard_screening_rules(
            frozen_job_json, classified
        ),
        "traces": traces,
        "degraded": False,
    }


def classify_job_requirements_batch(
    jobs: list[dict[str, Any]],
    *,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一次请求分类多个岗位，返回结果仍按 ``job_key`` 隔离。

    模型只返回每条原文要求的分类 ``items``；硬筛规则由本地从已校验的 items
    派生。这样一次请求可以覆盖多个岗位，同时不会让模型直接生成淘汰策略。
    """
    prepared: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for raw_job in jobs:
        job_key = str(raw_job.get("job_key") or "").strip()
        if not job_key:
            raise LLMResponseError("job_requirement_classification_job_key_missing")
        frozen = {
            "title": str(raw_job.get("title") or ""),
            "education_requirement": raw_job.get("education_requirement"),
            "major_requirement": raw_job.get("major_requirement"),
            "major_requirement_source_quote": raw_job.get(
                "major_requirement_source_quote"
            ),
            "qualifications": list(raw_job.get("qualifications") or []),
        }
        sources = prepare_requirement_sources(frozen)
        classified: list[dict[str, Any]] = []
        job_pending: list[dict[str, Any]] = []
        for source in sources:
            item = _classify_deterministic(source, frozen)
            (classified if item is not None else job_pending).append(item or source)
        pending.extend([
            {**item, "job_key": job_key}
            for item in job_pending
        ])
        prepared.append({
            "job_key": job_key,
            "frozen": frozen,
            "classified": classified,
            "pending": job_pending,
        })

    traces: list[dict[str, Any]] = []
    model_by_job: dict[str, list[dict[str, Any]]] = {}
    invalid_job_keys: set[str] = set()
    if pending:
        payload, trace = call_json_llm(
            workflow_name=WORKFLOW_NAME,
            schema_name="job_requirement_classification_batch_v1",
            settings_overrides=_json_object_call_config(llm_config or {}),
            json_schema=_batch_response_schema(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "逐岗位分类输入中的任职要求，不补写原文没有的要求。"
                        "每个 job_key 必须恰好返回一次；每条 source_id 必须来自对应岗位且恰好返回一次；"
                        "source_quote 必须完整复制 source_text。明确淘汰性学历、年限、证书或资格才是 hard_screen；"
                        "技术和经验要求归入 required_capability 或 preferred_capability，健康品行等归入 non_scoring。"
                        "只返回 JSON：{\"jobs\":[{\"job_key\":\"...\",\"items\":[{\"source_id\":\"...\","
                        "\"source_quote\":\"...\",\"category\":\"...\",\"criterion_code\":null,"
                        "\"expected_value\":null}]}]}。category 只能是 hard_screen、required_capability、"
                        "preferred_capability、non_scoring；criterion_code 只能是 minimum_degree、"
                        "minimum_experience_years、project_experience、required_skill、certification、custom。"
                        "hard_screen 必须提供 criterion_code 和 expected_value；其他分类两字段必须为 null。"
                        "不得返回解释、Markdown或任何额外字段。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "jobs": [
                                {
                                    "job_key": item["job_key"],
                                    "title": item["frozen"]["title"],
                                    "requirements": [
                                        {
                                            "source_id": source["source_id"],
                                            "source_text": source["source_quote"],
                                        }
                                        for source in item["pending"]
                                    ],
                                }
                                for item in prepared
                                if item["pending"]
                            ],
                            "allowed_categories": sorted(CATEGORIES),
                            "allowed_criterion_codes": sorted(CRITERION_CODES),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        traces.append({**trace, "stage": "job_requirement_classification_batch"})
        model_by_job, invalid_job_keys = _validate_batch_model_result(payload, pending)

    output_jobs: list[dict[str, Any]] = []
    for item in prepared:
        job_key = item["job_key"]
        if job_key in invalid_job_keys:
            fallback = deterministic_requirement_fallback(item["frozen"])
            output_jobs.append({
                "job_key": job_key,
                **fallback,
                "traces": traces,
                "degraded": True,
            })
            continue
        classified = list(item["classified"])
        classified.extend(model_by_job.get(job_key, []))
        classified.sort(key=lambda row: (row["source_index"], row["atom_index"]))
        frozen = item["frozen"]
        output_jobs.append({
            "job_key": job_key,
            "items": classified,
            "hardScreeningRules": build_hard_screening_rules(frozen, classified),
            "traces": traces,
            "degraded": False,
        })
    return {
        "jobs": output_jobs,
        "traces": traces,
        "degraded": bool(invalid_job_keys),
    }


def deterministic_requirement_fallback(
    frozen_job_json: dict[str, Any],
) -> dict[str, Any]:
    """Publish explicit rules and conservatively exclude ambiguous qualifications."""
    items = []
    for source in prepare_requirement_sources(frozen_job_json):
        items.append(
            _classify_deterministic(source, frozen_job_json)
            or {
                **source,
                "category": "non_scoring",
                "criterion_code": None,
                "expected_value": None,
            }
        )
    return {
        "items": items,
        "hardScreeningRules": build_hard_screening_rules(frozen_job_json, items),
        "traces": [],
        "degraded": True,
    }


def prepare_requirement_sources(
    frozen_job_json: dict[str, Any],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source_index, raw in enumerate(
        _as_text_list(frozen_job_json.get("qualifications")), start=1
    ):
        atoms = _split_atoms(raw) or [raw]
        for atom_index, quote in enumerate(atoms, start=1):
            sources.append(
                {
                    "source_id": f"qualification:{source_index}:{atom_index}",
                    "source_index": source_index,
                    "atom_index": atom_index,
                    "source_quote": quote,
                }
            )
    return sources


def requires_llm_classification(frozen_job_json: dict[str, Any]) -> bool:
    """Return whether any atom is too ambiguous for the deterministic rules.

    The caller uses this to avoid creating an Activity checkpoint when a frozen JD
    is already fully classified locally. When it returns ``True``, the LLM call is
    still executed exclusively through the ActivityRunner boundary.
    """
    return any(
        _classify_deterministic(source, frozen_job_json) is None
        for source in prepare_requirement_sources(frozen_job_json)
    )


def build_hard_screening_rules(
    frozen_job_json: dict[str, Any],
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add_rule(*, source_key: str, criterion_code: str, expected_value: str | float, source_text: str) -> None:
        # 学历/年限可能同时出现在独立字段和任职要求中；同一条件只生成一条规则，
        # 避免用户确认时看到重复硬筛项并在评估时重复计算。
        dedupe_key = (criterion_code, json.dumps(expected_value, ensure_ascii=False, sort_keys=True, default=str))
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        rules.append(
            _hard_rule(
                source_key=source_key,
                criterion_code=criterion_code,
                expected_value=expected_value,
                source_text=source_text,
            )
        )

    education = str(frozen_job_json.get("education_requirement") or "").strip()
    # 只从明确的最低学历表达生成硬筛；“本科优先/硕士加分”等软条件
    # 仍可进入能力画像，但不能把候选人直接淘汰。
    degree = _degree_value(education)
    if degree:
        add_rule(
            source_key="education_requirement",
            criterion_code="minimum_degree",
            expected_value=degree,
            source_text=education,
        )
    for item in items:
        if item.get("category") != "hard_screen":
            continue
        criterion = str(item.get("criterion_code") or "")
        expected = item.get("expected_value")
        if criterion not in CRITERION_CODES or expected in (None, ""):
            continue
        add_rule(
            source_key=str(item["source_id"]),
            criterion_code=criterion,
            expected_value=expected,
            source_text=str(item["source_quote"]),
        )
    return rules


def _split_atoms(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"[；;\n]+|(?<=。)\s*", normalized)
    marker = re.compile(
        r"^\s*(?:(?:\d+|[一二三四五六七八九十]+)[.．、）)]|[（(]\d+[）)]|[•·●▪])\s*"
    )
    return [
        cleaned
        for chunk in chunks
        if (cleaned := marker.sub("", chunk).strip(" \t，,；;"))
    ]


def _classify_deterministic(
    source: dict[str, Any],
    frozen_job_json: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    text = str(source["source_quote"])
    preferred = bool(_PREFERRED.search(text))
    degree = _degree_value(text)
    if degree and (_EXPLICIT_REQUIRED.search(text) or not _PREFERRED.search(text)):
        return _classified(source, "hard_screen", "minimum_degree", degree)
    years = _YEARS.search(text)
    if years and not preferred:
        return _classified(
            source,
            "hard_screen",
            "minimum_experience_years",
            float(years.group("years")),
        )
    if _CERTIFICATION.search(text):
        if preferred:
            # “证书优先/有证书加分”不是淘汰条件，但仍是有业务意义的
            # 优先能力；保留它供画像和后续匹配展示，不能生成 hard_screen。
            return _classified(source, "preferred_capability")
        if _EXPLICIT_REQUIRED.search(text) or re.search(r"CET[-－]?\s*[46]|英语[四六]级", text):
            return _classified(
                source,
                "hard_screen",
                "certification",
                _certification_value(text),
            )
    if _CUSTOM_HARD.search(text) and _EXPLICIT_REQUIRED.search(text) and not preferred:
        return _classified(source, "hard_screen", "custom", text)
    if _is_major_requirement_clause(text, frozen_job_json or {}):
        # 专业路由已单独消费该信息；即使专业名称包含“技术/设计”，也不能重复生成能力 JDUnit。
        return _classified(source, "non_scoring")
    if _NON_SCORING.search(text):
        return _classified(source, "non_scoring")
    if _CAPABILITY.search(text):
        return _classified(
            source,
            "preferred_capability" if preferred else "required_capability",
        )
    if preferred:
        return _classified(source, "non_scoring")
    return None


def _is_major_requirement_clause(
    text: str,
    frozen_job_json: dict[str, Any],
) -> bool:
    """识别完整专业条款，不枚举专业名称，也不吞掉真正的能力描述。"""
    if _CAPABILITY_ACTION.search(text):
        return False

    normalized = _normalize_clause(text)
    provenance = (
        frozen_job_json.get("major_requirement"),
        frozen_job_json.get("major_requirement_source_quote"),
    )
    if normalized and any(
        normalized == _normalize_clause(value)
        for value in provenance
        if value
    ):
        return True
    return bool(_MAJOR.search(text))


def _normalize_clause(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).casefold()


def _certification_value(text: str) -> str:
    """Return the certificate itself, not unrelated text in the same clause."""

    cet_levels: list[str] = []
    for match in re.finditer(r"CET[-－]?\s*([46])|英语([四六])级", text, re.IGNORECASE):
        raw_level = match.group(1) or match.group(2)
        level = {"四": "4", "六": "6"}.get(raw_level, raw_level)
        value = f"CET-{level}"
        if value not in cet_levels:
            cet_levels.append(value)
    if cet_levels:
        return "、".join(cet_levels)

    matched = _CERTIFICATION.search(text)
    return matched.group(0).strip(" \t，,；;。") if matched else text


def _classified(
    source: dict[str, Any],
    category: str,
    criterion_code: str | None = None,
    expected_value: str | float | None = None,
) -> dict[str, Any]:
    return {
        **source,
        "category": category,
        "criterion_code": criterion_code,
        "expected_value": expected_value,
    }


def _validate_model_result(
    payload: dict[str, Any] | None,
    pending: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise LLMResponseError("job_requirement_classification_items_missing")
    by_id = {item["source_id"]: item for item in pending}
    returned: dict[str, dict[str, Any]] = {}
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            raise LLMResponseError("job_requirement_classification_item_invalid")
        source_id = str(raw.get("source_id") or "")
        source = by_id.get(source_id)
        if source is None or source_id in returned:
            raise LLMResponseError("job_requirement_classification_id_mismatch")
        if str(raw.get("source_quote") or "") != source["source_quote"]:
            raise LLMResponseError("job_requirement_classification_quote_mismatch")
        category = str(raw.get("category") or "")
        criterion = raw.get("criterion_code")
        expected = raw.get("expected_value")
        if category not in CATEGORIES:
            raise LLMResponseError("job_requirement_classification_category_invalid")
        if category == "hard_screen":
            if str(criterion or "") not in CRITERION_CODES or expected in (None, ""):
                raise LLMResponseError("job_requirement_classification_hard_rule_invalid")
        elif criterion is not None or expected is not None:
            raise LLMResponseError("job_requirement_classification_non_hard_fields_invalid")
        returned[source_id] = _classified(
            source,
            category,
            str(criterion) if criterion is not None else None,
            expected,
        )
    if set(returned) != set(by_id):
        raise LLMResponseError("job_requirement_classification_coverage_mismatch")
    return [returned[item["source_id"]] for item in pending]


def _batch_response_schema() -> dict[str, Any]:
    """描述批量传输形状，但把逐岗位字段合法性交给业务校验。

    若 response_format 对嵌套字段做强校验，任一岗位出错都会让整个模型请求失败；
    这里列出字段名辅助模型生成，完整性、枚举和原文引用按岗位校验，才能局部降级。
    """
    return {
        "type": "object",
        "required": ["jobs"],
        "additionalProperties": False,
        "properties": {
            "jobs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_key": {},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source_id": {},
                                    "source_quote": {},
                                    "category": {},
                                    "criterion_code": {},
                                    "expected_value": {},
                                },
                            },
                        },
                    },
                },
            }
        },
    }


def _validate_batch_model_result(
    payload: dict[str, Any] | None,
    pending: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """按岗位校验覆盖和原文引用；坏岗位不污染同批其他岗位。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise LLMResponseError("job_requirement_classification_batch_invalid")
    expected: dict[str, dict[str, dict[str, Any]]] = {}
    for source in pending:
        expected.setdefault(str(source["job_key"]), {})[str(source["source_id"])] = source
    returned: dict[str, list[dict[str, Any]]] = {}
    invalid: set[str] = set()
    seen_jobs: set[str] = set()
    for raw_job in payload["jobs"]:
        if not isinstance(raw_job, dict):
            continue
        if set(raw_job) != {"job_key", "items"}:
            candidate_key = str(raw_job.get("job_key") or "")
            if candidate_key in expected:
                invalid.add(candidate_key)
            continue
        job_key = str(raw_job.get("job_key") or "")
        if job_key not in expected:
            continue
        if job_key in seen_jobs or not isinstance(raw_job.get("items"), list):
            invalid.add(job_key)
            continue
        seen_jobs.add(job_key)
        rows: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        job_valid = True
        for raw in raw_job["items"]:
            if not isinstance(raw, dict):
                job_valid = False
                break
            if set(raw) != {
                "source_id",
                "source_quote",
                "category",
                "criterion_code",
                "expected_value",
            }:
                job_valid = False
                break
            source_id = str(raw.get("source_id") or "")
            source = expected[job_key].get(source_id)
            if source is None or source_id in seen_sources:
                job_valid = False
                break
            if str(raw.get("source_quote") or "") != str(source["source_quote"]):
                job_valid = False
                break
            category = str(raw.get("category") or "")
            criterion = raw.get("criterion_code")
            expected_value = raw.get("expected_value")
            if category not in CATEGORIES:
                job_valid = False
                break
            if category == "hard_screen":
                if str(criterion or "") not in CRITERION_CODES or expected_value in (None, ""):
                    job_valid = False
                    break
            elif criterion is not None or expected_value is not None:
                job_valid = False
                break
            rows.append(_classified(source, category, str(criterion) if criterion is not None else None, expected_value))
            seen_sources.add(source_id)
        if not job_valid or seen_sources != set(expected[job_key]):
            invalid.add(job_key)
            continue
        returned[job_key] = rows
    invalid.update(set(expected) - set(returned))
    for job_key in invalid:
        returned.pop(job_key, None)
    return returned, invalid


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["items"],
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "source_id",
                        "source_quote",
                        "category",
                        "criterion_code",
                        "expected_value",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "source_id": {"type": "string", "minLength": 1},
                        "source_quote": {"type": "string", "minLength": 1},
                        "category": {"type": "string", "enum": sorted(CATEGORIES)},
                        "criterion_code": {
                            "type": ["string", "null"],
                            "enum": [*sorted(CRITERION_CODES), None],
                        },
                        "expected_value": {"type": ["string", "number", "null"]},
                    },
                },
            }
        },
    }


def _hard_rule(
    *,
    source_key: str,
    criterion_code: str,
    expected_value: str | float,
    source_text: str,
) -> dict[str, Any]:
    names = {
        "minimum_degree": "最低学历",
        "minimum_experience_years": "相关工作经验",
        "project_experience": "相关项目经验",
        "required_skill": "必备技能",
        "certification": "证书/资质",
        "custom": "自定义条件",
    }
    scopes = {
        "minimum_degree": "education",
        "minimum_experience_years": "work_experience",
        "project_experience": "project_experience",
        "required_skill": "skills",
        "certification": "certifications",
        "custom": "full_resume",
    }
    operators = {
        "minimum_degree": "degree_at_least",
        "minimum_experience_years": "years_at_least",
    }
    digest = hashlib.sha256(
        f"{source_key}:{criterion_code}:{expected_value}".encode("utf-8")
    ).hexdigest()[:20].upper()
    return {
        "rule_id": f"HSRULE_AUTO_{digest}",
        "criterion_type": criterion_code,
        "name": names[criterion_code],
        "source_scope": scopes[criterion_code],
        "operator": operators.get(criterion_code, "semantic_match"),
        "expected_value": expected_value,
        "description": source_text[:500],
        "enabled": True,
        "schema_version": "hard_screening_rule_v1",
    }


def _degree_value(text: str) -> str | None:
    """提取最低学历，不把“优先/加分”误当成淘汰条件。

    先按短语拆分，优先识别带“及以上/至少/最低”的明确下限；
    同一要求中同时出现“本科及以上、硕士优先”时返回本科。
    """
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    clauses = [part.strip() for part in re.split(r"[，,；;。\n、]+", normalized) if part.strip()]
    levels = {"大专": 1, "专科": 1, "本科": 2, "学士": 2, "硕士": 3, "研究生": 3, "博士": 4}
    explicit: list[tuple[int, str]] = []
    fallback: list[tuple[int, str]] = []
    for clause in clauses:
        matches = list(re.finditer(r"博士|硕士|研究生|本科|学士|大专|专科", clause))
        if not matches:
            continue
        soft = bool(_PREFERRED.search(clause))
        for match in matches:
            value = match.group(0)
            canonical = "大专" if value == "专科" else "本科" if value == "学士" else "硕士" if value == "研究生" else value
            suffix = clause[match.end():]
            is_lower_bound = bool(re.search(r"及以上|以上|至少|最低|起步", suffix))
            # 单独的 education_requirement 字段通常就是要求；但含软词的
            # 片段（例如“硕士优先”）必须排除，避免误生成硬筛。
            is_explicit = is_lower_bound or (not soft and len(matches) == 1 and bool(_EXPLICIT_REQUIRED.search(clause)))
            if is_explicit:
                explicit.append((levels[canonical], canonical))
            elif not soft:
                fallback.append((levels[canonical], canonical))
    candidates = explicit or fallback
    if not candidates:
        return None
    # 多个明确条件取最低者，保证“本科及以上、硕士优先”仍为本科。
    return min(candidates, key=lambda item: item[0])[1]


def _as_text_list(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [] if value is None else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _json_object_call_config(config: dict[str, Any]) -> dict[str, Any]:
    overrides = dict(config)
    workflows = {
        key: dict(value)
        for key, value in (overrides.get("workflows") or {}).items()
        if isinstance(value, dict)
    }
    workflows[WORKFLOW_NAME] = {
        **workflows.get(WORKFLOW_NAME, {}),
        "json_mode": True,
        "strict_json_schema": False,
        "temperature": 0,
    }
    overrides["workflows"] = workflows
    return overrides
