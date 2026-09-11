from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db.session import SessionLocal
from backend.app.models.entities import (
    InterviewGuide,
    RequirementAssessment,
    Risk,
    VerificationTarget,
    WorkflowArtifact,
)


def _dedupe_rows(db, model, *id_keys: str) -> int:
    removed = 0
    seen: set[tuple[str, str]] = set()
    for row in db.scalars(select(model).order_by(model.id)).all():
        identity = next((str(row.payload.get(key)) for key in id_keys if row.payload.get(key)), "")
        if not identity:
            continue
        key = (row.application_id, identity)
        if key in seen:
            db.delete(row)
            removed += 1
        else:
            seen.add(key)
    return removed


def _unique_strings(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return list(dict.fromkeys(item for item in value if item))


def _repair_payload(value: Any) -> Any:
    if isinstance(value, list):
        repaired = [_repair_payload(item) for item in value]
        if all(isinstance(item, dict) for item in repaired):
            id_key = next(
                (key for key in ("targetId", "target_id") if any(item.get(key) for item in repaired)),
                None,
            )
            if id_key:
                seen: set[str] = set()
                unique: list[Any] = []
                for item in repaired:
                    identity = str(item.get(id_key) or "")
                    if identity and identity in seen:
                        continue
                    if identity:
                        seen.add(identity)
                    unique.append(item)
                return unique
        return repaired
    if not isinstance(value, dict):
        return value

    repaired = {key: _repair_payload(item) for key, item in value.items()}
    for key in ("verificationTargetIds", "source_verification_target_ids", "verification_target_ids"):
        if key in repaired:
            repaired[key] = _unique_strings(repaired[key])
    return repaired


def main() -> None:
    with SessionLocal() as db:
        removed = {
            "requirements": _dedupe_rows(db, RequirementAssessment, "requirementId", "requirement_id"),
            "risks": _dedupe_rows(db, Risk, "riskId", "risk_id"),
            "verification_targets": _dedupe_rows(db, VerificationTarget, "targetId", "target_id"),
        }
        repaired_guides = 0
        for row in db.scalars(select(InterviewGuide)).all():
            repaired = _repair_payload(copy.deepcopy(row.payload))
            if repaired != row.payload:
                row.payload = repaired
                repaired_guides += 1
        repaired_artifacts = 0
        for row in db.scalars(select(WorkflowArtifact).where(WorkflowArtifact.artifact_json.is_not(None))).all():
            repaired = _repair_payload(copy.deepcopy(row.artifact_json))
            if repaired != row.artifact_json:
                row.artifact_json = repaired
                repaired_artifacts += 1
        db.commit()
        print({**removed, "guides": repaired_guides, "artifacts": repaired_artifacts})


if __name__ == "__main__":
    main()
