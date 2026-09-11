from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.init_db import init_db
from backend.app.db.session import SessionLocal
from backend.app.models.entities import UniversityRankingEntry


DEFAULT_DATA_PATH = Path(__file__).with_name("data") / "softke_bcur_2026.json"


def load_ranking_dataset(path: Path = DEFAULT_DATA_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != 200:
        raise ValueError("ranking_dataset_must_contain_200_schools")
    names = [str(item.get("canonical_name") or "").strip() for item in entries]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("ranking_dataset_school_names_invalid")
    ranks = [int(item["rank"]) for item in entries]
    if ranks != sorted(ranks) or ranks[0] != 1 or ranks[-1] > 200:
        raise ValueError("ranking_dataset_ranks_invalid")
    return payload


def import_university_rankings(
    db: Session,
    *,
    path: Path = DEFAULT_DATA_PATH,
) -> dict[str, int | str]:
    payload = load_ranking_dataset(path)
    dataset_version = str(payload["dataset_version"])
    ranking_source = str(payload["ranking_source"])
    ranking_year = int(payload["ranking_year"])
    existing = list(
        db.scalars(
            select(UniversityRankingEntry).where(
                UniversityRankingEntry.dataset_version == dataset_version
            )
        ).all()
    )
    by_name = {item.canonical_name: item for item in existing}
    incoming_names: set[str] = set()
    inserted = 0
    updated = 0
    for item in payload["entries"]:
        name = str(item["canonical_name"]).strip()
        incoming_names.add(name)
        row = by_name.get(name)
        if row is None:
            row = UniversityRankingEntry(
                entry_id=_entry_id(dataset_version, name),
                dataset_version=dataset_version,
                ranking_source=ranking_source,
                ranking_year=ranking_year,
                canonical_name=name,
                aliases={"items": list(item.get("aliases") or [])},
                rank=int(item["rank"]),
                school_score=_score_from_rank(int(item["rank"])),
            )
            db.add(row)
            inserted += 1
        else:
            row.ranking_source = ranking_source
            row.ranking_year = ranking_year
            row.aliases = {"items": list(item.get("aliases") or [])}
            row.rank = int(item["rank"])
            row.school_score = _score_from_rank(row.rank)
            updated += 1
    deleted = 0
    for row in existing:
        if row.canonical_name not in incoming_names:
            db.delete(row)
            deleted += 1
    db.commit()
    return {
        "dataset_version": dataset_version,
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "total": len(payload["entries"]),
    }


def _entry_id(dataset_version: str, name: str) -> str:
    digest = hashlib.sha256(f"{dataset_version}|{name}".encode("utf-8")).hexdigest()[:20]
    return f"URE_{digest}"


def _score_from_rank(rank: int) -> float:
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
    if rank <= 200:
        return 0.70
    return 0.65


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the versioned university ranking dataset.")
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_PATH)
    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        result = import_university_rankings(db, path=args.data_file)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
