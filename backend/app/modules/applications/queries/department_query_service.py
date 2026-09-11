from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Department


class ApplicationDepartmentQueryService:
    """Read-only department options used by application intake screens."""

    def __init__(self, db: Session):
        self.db = db

    def list_options(self) -> list[dict[str, str]]:
        departments = self.db.scalars(
            select(Department)
            .where(Department.deleted_at.is_(None))
            .order_by(Department.department_id)
        ).all()
        return [
            {"departmentId": item.department_id, "name": item.name}
            for item in departments
        ]
