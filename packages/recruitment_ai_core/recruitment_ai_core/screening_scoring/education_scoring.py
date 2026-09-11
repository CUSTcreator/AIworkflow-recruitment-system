from __future__ import annotations

import re
from typing import Any

from .profile_evidence import education_records


RANKING_DATASET_VERSION = "softke_bcur_2026"
UNKNOWN_DOMESTIC_SCHOOL_SCORE = 0.65
DEGREE_WEIGHTS = {"undergraduate": 0.60, "master": 0.40}


def score_education(
    resume_profile: dict[str, Any],
    ranking_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    facts = education_records(resume_profile)
    entries = ranking_entries or []
    evidence_ids = [
        str(ref.get("block_id"))
        for item in facts
        for ref in list(item.get("source_refs") or [])
        if isinstance(ref, dict) and ref.get("block_id")
    ]
    if not facts:
        return _education_result(
            score=UNKNOWN_DOMESTIC_SCHOOL_SCORE,
            status="not_provided",
            undergraduate=None,
            master=None,
            evidence_ids=[],
            records=[],
            verification_reason="简历未提供可识别的教育经历",
        )
    if not entries:
        return _education_result(
            score=UNKNOWN_DOMESTIC_SCHOOL_SCORE,
            status="ranking_unavailable",
            undergraduate=None,
            master=None,
            evidence_ids=evidence_ids,
            records=[],
            verification_reason="软科排名数据未加载，学历分暂按基准值处理",
        )

    records = build_education_records(facts, entries)
    undergraduate = _best_record_score(records, "undergraduate")
    master = _best_record_score(records, "master")
    inferred_school_name: str | None = None
    verification_reason: str | None = None
    degree_inference: str | None = None
    if undergraduate is None and master is None:
        inferred_record = _best_ranked_record(records)
        if inferred_record is not None:
            undergraduate = float(inferred_record["school_score"])
            inferred_school_name = str(inferred_record["school_name"])
            score = undergraduate
            status = "degree_inferred"
            degree_inference = "教育经历包含可识别学校，但原文未明确学历层次，暂按本科计分"
            verification_reason = "学历层次待核验"
        else:
            score = UNKNOWN_DOMESTIC_SCHOOL_SCORE
            status = "school_unknown"
            verification_reason = "学校名称脱敏、未进入排名或无法识别"
    elif master is not None:
        undergraduate_value = undergraduate if undergraduate is not None else UNKNOWN_DOMESTIC_SCHOOL_SCORE
        score = (
            DEGREE_WEIGHTS["undergraduate"] * undergraduate_value
            + DEGREE_WEIGHTS["master"] * master
        )
        if undergraduate is None:
            status = "undergraduate_missing"
            verification_reason = "已识别硕士教育经历，但本科院校信息缺失"
        else:
            status = "scored"
    else:
        score = undergraduate if undergraduate is not None else UNKNOWN_DOMESTIC_SCHOOL_SCORE
        status = "scored"
    return _education_result(
        score=score,
        status=status,
        undergraduate=undergraduate,
        master=master,
        evidence_ids=evidence_ids,
        records=records,
        inferred_school_name=inferred_school_name,
        degree_inference=degree_inference,
        verification_reason=verification_reason,
    )


def score_from_rank(rank: int | None) -> float:
    if rank is None or rank > 200:
        return 0.65
    if rank <= 10:
        return 1.00
    if rank <= 30:
        return 0.95
    if rank <= 60:
        return 0.90
    if rank <= 100:
        return 0.84
    if rank <= 150:
        return 0.78
    return 0.70


def build_education_records(
    facts: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(
        facts,
        key=lambda item: (
            int(item.get("source_line_start") or 0),
            int(item.get("subspan_index") or 0),
        ),
    )
    school_markers: list[tuple[int, dict[str, Any]]] = []
    for index, fact in enumerate(ordered):
        entry = _match_ranked_school(_education_text(fact), entries)
        if entry is not None:
            school_markers.append((index, entry))

    if not school_markers:
        return [
            {
                "education_record_id": f"EDU_{index:03d}",
                "school_name": None,
                "degree_level": degree_level,
                "rank": None,
                "school_score": None,
                "raw_text": _education_text(fact),
                "source_span_ids": _source_ids(fact),
            }
            for index, fact in enumerate(ordered, start=1)
            if (degree_level := _degree_level(_education_text(fact))) is not None
        ]

    buckets: dict[int, list[dict[str, Any]]] = {position: [] for position, _ in school_markers}
    marker_positions = [position for position, _ in school_markers]
    for index, fact in enumerate(ordered):
        nearest_position = min(
            marker_positions,
            key=lambda position: (abs(position - index), position > index),
        )
        buckets[nearest_position].append(fact)

    records: list[dict[str, Any]] = []
    for record_index, (position, entry) in enumerate(school_markers, start=1):
        record_facts = buckets[position]
        raw_text = "\n".join(_education_text(item) for item in record_facts).strip()
        rank = int(entry["rank"])
        records.append(
            {
                "education_record_id": f"EDU_{record_index:03d}",
                "school_name": str(entry.get("canonical_name") or "").strip(),
                "degree_level": _degree_level(raw_text),
                "rank": rank,
                "school_score": score_from_rank(rank),
                "raw_text": raw_text,
                "source_span_ids": [
                    source_id
                    for item in record_facts
                    for source_id in _source_ids(item)
                ],
            }
        )
    return records


def _education_result(
    *,
    score: float,
    status: str,
    undergraduate: float | None,
    master: float | None,
    evidence_ids: list[str],
    records: list[dict[str, Any]],
    inferred_school_name: str | None = None,
    degree_inference: str | None = None,
    verification_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "score": round(score * 100, 2),
        "normalized_score": round(score, 6),
        "status": status,
        "ranking_dataset_version": RANKING_DATASET_VERSION,
        "undergraduate_school_score": undergraduate,
        "master_school_score": master,
        "inferred_school_name": inferred_school_name,
        "degree_inference": degree_inference,
        "verification_required": status != "scored",
        "verification_reason": verification_reason,
        "supporting_evidence_ids": evidence_ids,
        "education_records": records,
    }


def _best_record_score(records: list[dict[str, Any]], degree_level: str) -> float | None:
    scores = [
        float(item["school_score"])
        for item in records
        if item.get("degree_level") == degree_level and item.get("school_score") is not None
    ]
    return max(scores) if scores else None


def _best_ranked_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked = [item for item in records if item.get("school_score") is not None]
    return max(ranked, key=lambda item: float(item["school_score"])) if ranked else None


def _source_ids(fact: dict[str, Any]) -> list[str]:
    span_id = str(fact.get("span_id") or "").strip()
    if span_id:
        return [span_id]
    return [
        str(ref.get("block_id"))
        for ref in list(fact.get("source_refs") or [])
        if isinstance(ref, dict) and str(ref.get("block_id") or "").strip()
    ]


def _education_text(fact: dict[str, Any]) -> str:
    raw = str(fact.get("raw_text") or fact.get("text") or "").strip()
    if raw:
        return raw
    degree_labels = {
        "associate": "大专",
        "bachelor": "本科",
        "undergraduate": "本科",
        "master": "硕士",
        "doctorate": "博士",
    }
    return " ".join(
        str(fact.get(key) or "").strip()
        for key in ("school", "major", "degree")
        if str(fact.get(key) or "").strip()
    ) + (
        f" {degree_labels.get(str(fact.get('degree_level') or ''), '')}"
        if degree_labels.get(str(fact.get("degree_level") or ""), "")
        else ""
    )


def _match_ranked_school(text: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_text = _normalize_school_name(text)
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for entry in entries:
        aliases = entry.get("aliases") or []
        if isinstance(aliases, dict):
            aliases = aliases.get("items", [])
        names = [entry.get("canonical_name"), *aliases]
        matched_lengths = [
            len(normalized_name)
            for name in names
            if name
            and (normalized_name := _normalize_school_name(str(name)))
            and normalized_name in normalized_text
        ]
        if matched_lengths:
            matches.append((max(matched_lengths), -int(entry["rank"]), entry))
    return max(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


def _degree_level(text: str) -> str | None:
    lowered = text.casefold()
    if any(cue in lowered for cue in ("博士", "doctorate", "phd")):
        return "doctorate"
    if any(cue in lowered for cue in ("硕士", "研究生", "master")):
        return "master"
    if any(cue in lowered for cue in ("本科", "学士", "bachelor", "undergraduate")):
        return "undergraduate"
    return None


def _normalize_school_name(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("(", "（").replace(")", "）").lower()
