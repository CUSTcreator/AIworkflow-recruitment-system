from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    CandidateCapabilityProfileRecord,

    ScoreSnapshot,
)


class ScoreSnapshotRepository:
    """Append-only persistence for application score versions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def append(
        self,
        *,
        score_snapshot_id: str,
        application_id: str,
        stage: str,
        score: int,
        result_json: dict,
    ) -> ScoreSnapshot:
        self.db.flush()
        previous = self.db.scalar(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.application_id == application_id)
            .order_by(ScoreSnapshot.version.desc(), ScoreSnapshot.created_at.desc())
            .with_for_update()
        )
        capability = self.db.scalar(
            select(CandidateCapabilityProfileRecord)
            .where(CandidateCapabilityProfileRecord.application_id == application_id)
            .order_by(CandidateCapabilityProfileRecord.version.desc())
        )

        snapshot = ScoreSnapshot(
            score_snapshot_id=score_snapshot_id,
            application_id=application_id,
            stage=stage,
            version=(previous.version + 1) if previous else 1,
            previous_snapshot_id=previous.score_snapshot_id if previous else None,

            capability_profile_id=capability.profile_id if capability else None,
            score=score,
            result_json=result_json,
        )
        self.db.add(snapshot)
        return snapshot

    def latest(self, application_id: str, *, stage: str | None = None) -> ScoreSnapshot | None:
        query = select(ScoreSnapshot).where(ScoreSnapshot.application_id == application_id)
        if stage is not None:
            query = query.where(ScoreSnapshot.stage == stage)
        return self.db.scalar(query.order_by(ScoreSnapshot.version.desc(), ScoreSnapshot.created_at.desc()))