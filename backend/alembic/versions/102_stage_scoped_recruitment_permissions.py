"""Replace generic recruitment permissions with stage-scoped permissions.

Revision ID: 102_stage_scoped_recruitment_permissions
Revises: 101_candidate_workspace_documents

Roles persist expanded atomic permissions, while the administration UI configures
responsibility bundles.  This migration therefore updates both role defaults and
individual allow/deny overrides so that an old permission cannot remain as a
hidden runtime capability after it disappears from the UI catalog.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "102_stage_scoped_recruitment_permissions"
down_revision = "101_candidate_workspace_documents"
branch_labels = None
depends_on = None


RETIRED_PERMISSION_CODES = frozenset({
    "analytics.view",
    "candidate.advance",
    "candidate.reject",
})
LEGACY_OVERRIDE_CODES = RETIRED_PERMISSION_CODES | {"hard_screening.manage"}
STAGE_REVIEW_PERMISSION_CODES = (
    "hard_screening.review",
    "department_review.manage",
)
BUILTIN_ROLE_PERMISSION_CODES = {
    # 内置角色的历史原子权限曾与页面职责包不一致。这里按目标职责包写入
    # 完整、可投影的集合，不能把 HR 的旧推进/淘汰码机械改名后继续保留。
    "department_manager": {"candidate.view"},
    "department_recruiter": {
        "candidate.view",
        "resume.upload",
        "resume_submission.manage",
        "application.create",
        "candidate_document.upload",
        "candidate_document.manage",
        "screening.run",
        "hard_screening.review",
        "department_review.manage",
        "first_interview.manage",
    },
    "hr": {
        "candidate.view",
        "resume.upload",
        "resume_submission.manage",
        "application.create",
        "candidate_document.upload",
        "candidate_document.manage",
        "job_document.upload",
        "job_document.confirm",
        "job.edit",
        "job.delete",
        "hard_screening.policy.manage",
        "hard_screening.catalog.manage",
        "interview_guide.manage",
        "second_interview.manage",
        "final_decision.manage",
        "application.delete",
    },
}


def _permissions(value: object) -> dict[str, object]:
    if isinstance(value, str):
        return dict(json.loads(value or "{}"))
    return dict(value or {})


def _enabled(value: object) -> bool:
    return value is True


def _merge_effect(current: str | None, incoming: str) -> str:
    """Merge migration results conservatively: an explicit deny always wins."""
    if current == "deny" or incoming == "deny":
        return "deny"
    return "allow"


def _migrate_role_permissions(
    role_id: str, raw_permissions: object
) -> dict[str, object]:
    """Translate old role JSON without leaving retired codes behind.

    ``candidate.advance`` and ``candidate.reject`` used to authorize different
    outcomes in several unrelated stages.  The new model makes each stage a
    single responsibility, so either legacy code grants the corresponding
    stage responsibility.  This preserves existing operating roles while the
    responsibility-bundle UI now exposes the real capability explicitly.
    """
    if role_id in BUILTIN_ROLE_PERMISSION_CODES:
        return {code: True for code in BUILTIN_ROLE_PERMISSION_CODES[role_id]}

    permissions = _permissions(raw_permissions)
    advance_enabled = _enabled(permissions.pop("candidate.advance", None))
    reject_enabled = _enabled(permissions.pop("candidate.reject", None))
    policy_enabled = permissions.pop("hard_screening.manage", None)
    permissions.pop("analytics.view", None)

    if policy_enabled is not None:
        permissions.setdefault("hard_screening.policy.manage", policy_enabled)
    if _enabled(permissions.get("resume.upload")):
        # The old upload permission also guarded existing-submission repairs.
        # A role that already had it keeps that operational capability.
        permissions.setdefault("resume_submission.manage", True)
    if advance_enabled or reject_enabled:
        for code in STAGE_REVIEW_PERMISSION_CODES:
            permissions.setdefault(code, True)
    if advance_enabled:
        permissions.setdefault("application.create", True)
    return permissions


def _replace_legacy_user_overrides(bind: sa.Connection) -> None:
    """Rewrite only legacy override rows and preserve unrelated exceptions."""
    rows = bind.execute(
        sa.text(
            "SELECT permission_override_id, user_id, permission_code, effect "
            "FROM user_permission_overrides"
        )
    ).mappings()
    by_user: dict[str, dict[str, str]] = {}
    existing_targets: dict[str, dict[str, str]] = {}
    for row in rows:
        user_id = str(row["user_id"])
        code = str(row["permission_code"])
        effect = str(row["effect"])
        if code in LEGACY_OVERRIDE_CODES:
            by_user.setdefault(user_id, {})[code] = effect
        else:
            existing_targets.setdefault(user_id, {})[code] = effect

    bind.execute(
        sa.text(
            "DELETE FROM user_permission_overrides "
            "WHERE permission_code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list(LEGACY_OVERRIDE_CODES)},
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    for user_id, legacy in by_user.items():
        targets = dict(existing_targets.get(user_id, {}))

        policy_effect = legacy.get("hard_screening.manage")
        if policy_effect is not None:
            targets["hard_screening.policy.manage"] = _merge_effect(
                targets.get("hard_screening.policy.manage"), policy_effect
            )

        # An old deny on resume.upload intentionally covered all later resume
        # corrections too.  An old allow remains upload-only because granting
        # a newly split management responsibility would be an expansion.
        if existing_targets.get(user_id, {}).get("resume.upload") == "deny":
            targets["resume_submission.manage"] = _merge_effect(
                targets.get("resume_submission.manage"), "deny"
            )

        advance_effect = legacy.get("candidate.advance")
        reject_effect = legacy.get("candidate.reject")
        stage_effects = {effect for effect in (advance_effect, reject_effect) if effect}
        if "deny" in stage_effects:
            stage_effect = "deny"
        elif "allow" in stage_effects:
            stage_effect = "allow"
        else:
            stage_effect = None
        if stage_effect is not None:
            for code in STAGE_REVIEW_PERMISSION_CODES:
                targets[code] = _merge_effect(targets.get(code), stage_effect)
        if advance_effect is not None:
            targets["application.create"] = _merge_effect(
                targets.get("application.create"), advance_effect
            )

        # Existing target rows were not deleted.  Update them when a legacy
        # counterpart changes their effect; otherwise insert a new override.
        for code, effect in targets.items():
            if code not in {
                "hard_screening.policy.manage",
                "resume_submission.manage",
                "hard_screening.review",
                "department_review.manage",
                "application.create",
            }:
                continue
            if code in existing_targets.get(user_id, {}):
                bind.execute(
                    sa.text(
                        "UPDATE user_permission_overrides SET effect = :effect, "
                        "updated_at = :updated_at "
                        "WHERE user_id = :user_id AND permission_code = :permission_code"
                    ),
                    {
                        "effect": effect,
                        "updated_at": now,
                        "user_id": user_id,
                        "permission_code": code,
                    },
                )
            elif code in targets:
                bind.execute(
                    sa.text(
                        "INSERT INTO user_permission_overrides "
                        "(permission_override_id, user_id, permission_code, effect, created_at, updated_at) "
                        "VALUES (:permission_override_id, :user_id, :permission_code, :effect, :created_at, :updated_at)"
                    ),
                    {
                        "permission_override_id": f"UPO_{uuid4().hex[:20].upper()}",
                        "user_id": user_id,
                        "permission_code": code,
                        "effect": effect,
                        "created_at": now,
                        "updated_at": now,
                    },
                )


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT role_id, permissions FROM role_definitions")
    ).mappings()
    for row in rows:
        permissions = _migrate_role_permissions(str(row["role_id"]), row["permissions"])
        bind.execute(
            sa.text(
                "UPDATE role_definitions SET permissions = :permissions "
                "WHERE role_id = :role_id"
            ).bindparams(
                sa.bindparam(
                    "permissions",
                    type_=sa.JSON().with_variant(JSONB(), "postgresql"),
                )
            ),
            {"role_id": row["role_id"], "permissions": permissions},
        )
    _replace_legacy_user_overrides(bind)


def downgrade() -> None:
    raise RuntimeError(
        "102 将跨阶段权限拆分为阶段职责，无法在不重新引入隐藏授权的情况下自动降级；请从备份恢复。"
    )
