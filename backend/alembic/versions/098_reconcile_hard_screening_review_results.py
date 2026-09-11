"""Reconcile hard-screening results finalized by a human review.

Older review commands advanced ``applications`` but left the corresponding
``hard_screening_results`` row at ``manual_review``. The application row is the
audited workflow state, so this one-time repair aligns only that impossible
combination and preserves each automated rule conclusion under audit fields.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "098_reconcile_hard_screening_review_results"
down_revision = "097_job_draft_requirement_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE hard_screening_results AS result
        SET
            status = application.hard_screening_status,
            summary = COALESCE(
                NULLIF(application.hard_screening_summary, ''),
                result.summary
            ),
            rule_results_json = COALESCE(
                (
                    SELECT jsonb_agg(
                        CASE
                            WHEN item ->> 'status' = 'manual_review' THEN
                                item || jsonb_build_object(
                                    'automated_status', item -> 'status',
                                    'automated_reason', item -> 'reason',
                                    'status', application.hard_screening_status,
                                    'reason', '人工复核：' || COALESCE(
                                        NULLIF(application.hard_screening_summary, ''),
                                        '已完成人工复核'
                                    ),
                                    'reason_code', 'human_override'
                                )
                            ELSE item
                        END
                        ORDER BY ordinal
                    )
                    FROM jsonb_array_elements(
                        result.rule_results_json::jsonb
                    ) WITH ORDINALITY AS entries(item, ordinal)
                ),
                '[]'::jsonb
            ),
            updated_at = application.updated_at
        FROM applications AS application
        WHERE
            result.application_id = application.application_id
            AND result.policy_id = application.hard_screening_policy_id
            AND result.status = 'manual_review'
            AND application.hard_screening_status IN ('passed', 'failed')
    """))


def downgrade() -> None:
    # The previous values were internally inconsistent and cannot be
    # reconstructed without discarding the valid human decision.
    pass
