from __future__ import annotations

from typing import Literal


JobAssignmentRole = Literal["hiring_manager", "department_recruiter"]


def missing_job_assignments(
    *,
    hiring_manager_id: str | None,
    department_recruiter_id: str | None,
) -> list[JobAssignmentRole]:
    """Return missing job-level owners in stable display order.

    ``setup_pending`` remains the workflow state. This projection only explains
    which owner configuration is missing and must never drive job transitions.
    """
    missing: list[JobAssignmentRole] = []
    if not hiring_manager_id:
        missing.append("hiring_manager")
    if not department_recruiter_id:
        missing.append("department_recruiter")
    return missing

