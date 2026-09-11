from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from backend.app.modules.applications.public import ApplicationReadModelSource, ApplicationReadSource


class DecisionSummaryQueryService:
    def __init__(self, db: Session) -> None:
        self.source = ApplicationReadModelSource(db)

    def latest(
        self,
        application_id: str,
        *,
        source: ApplicationReadSource | None = None,
    ) -> dict[str, Any] | None:
        # ``source`` is retained for the stable query-service signature; current
        # presentation artifacts no longer need score snapshots to repair a
        # missing recommendation. Legacy artifacts are intentionally ignored.
        del source
        row = self.source.latest_artifact(
            application_id, "ai_decision_summary"
        )
        if not row or not isinstance(row.artifact_json, dict):
            return None
        summary = deepcopy(row.artifact_json)
        summary.pop("trace", None)
        summary.pop("warnings", None)
        return summary if summary.get("summary_version") else None


