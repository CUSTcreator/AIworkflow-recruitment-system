from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Candidate, CandidateProfile


def _text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def normalize_phone(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits if re.fullmatch(r"1[3-9]\d{9}", digits) else ""


def normalize_email(value: object) -> str:
    value = str(value or "").strip().casefold()
    return value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else ""


@dataclass(frozen=True)
class CandidateIdentity:
    name: str
    school: str
    major: str
    phone: str
    email: str

    @property
    def has_contact(self) -> bool:
        return bool(self.phone or self.email)


class CandidateIdentityService:
    """Strict identity matching; deliberately never uses semantic similarity."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def find_matches(self, identity: CandidateIdentity) -> tuple[list[Candidate], bool]:
        if not identity.has_contact or not all((identity.name, identity.school, identity.major)):
            return [], False
        matches: list[Candidate] = []
        conflict = False
        profiles = {
            profile.candidate_id: profile
            for profile in self.db.scalars(select(CandidateProfile)).all()
        }
        # 已删除档案仅保留审计价值，不能再参与重复候选人身份匹配。
        for candidate in self.db.scalars(
            select(Candidate).where(Candidate.status != "archived")
        ).all():
            profile = profiles.get(candidate.candidate_id)
            school = profile.school if profile is not None else ""
            major = profile.major if profile is not None else ""
            phone = profile.phone if profile is not None else ""
            email = profile.email if profile is not None else ""
            if (
                _text(candidate.display_name) != identity.name
                or _text(school) != identity.school
                or _text(major) != identity.major
            ):
                continue
            existing_phone = normalize_phone(phone)
            existing_email = normalize_email(email)
            phone_conflict = bool(identity.phone and existing_phone and identity.phone != existing_phone)
            email_conflict = bool(identity.email and existing_email and identity.email != existing_email)
            if phone_conflict or email_conflict:
                conflict = True
                continue
            if (identity.phone and identity.phone == existing_phone) or (
                identity.email and identity.email == existing_email
            ):
                matches.append(candidate)
        return matches, conflict
