"""version score snapshots

Revision ID: 036_version_score_snapshots
Revises: 035_interview_targets
"""

from alembic import op
import sqlalchemy as sa


revision = "036_version_score_snapshots"
down_revision = "035_interview_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("score_snapshots") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("previous_snapshot_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("evidence_snapshot_id", sa.String(length=96), nullable=True))
        batch.add_column(sa.Column("capability_profile_id", sa.String(length=96), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT score_snapshot_id, application_id FROM score_snapshots "
            "ORDER BY application_id, created_at, score_snapshot_id"
        )
    ).mappings().all()
    versions: dict[str, int] = {}
    previous: dict[str, str | None] = {}
    for row in rows:
        application_id = str(row["application_id"])
        version = versions.get(application_id, 0) + 1
        connection.execute(
            sa.text(
                "UPDATE score_snapshots SET version=:version, previous_snapshot_id=:previous "
                "WHERE score_snapshot_id=:snapshot_id"
            ),
            {
                "version": version,
                "previous": previous.get(application_id),
                "snapshot_id": row["score_snapshot_id"],
            },
        )
        versions[application_id] = version
        previous[application_id] = str(row["score_snapshot_id"])

    with op.batch_alter_table("score_snapshots") as batch:
        batch.drop_constraint("uq_score_application_stage", type_="unique")
        batch.alter_column("version", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_score_application_version", ["application_id", "version"])
        batch.create_foreign_key("fk_score_previous", "score_snapshots", ["previous_snapshot_id"], ["score_snapshot_id"])
        batch.create_foreign_key("fk_score_evidence", "candidate_evidence_snapshots", ["evidence_snapshot_id"], ["evidence_snapshot_id"])
        batch.create_foreign_key("fk_score_capability", "candidate_capability_profiles", ["capability_profile_id"], ["profile_id"])


def downgrade() -> None:
    naming_convention = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    }
    foreign_keys = {
        tuple(item["constrained_columns"]): item.get("name")
        for item in sa.inspect(op.get_bind()).get_foreign_keys("score_snapshots")
    }
    with op.batch_alter_table(
        "score_snapshots",
        naming_convention=naming_convention,
    ) as batch:
        batch.drop_constraint(
            foreign_keys.get(("capability_profile_id",))
            or "fk_score_snapshots_capability_profile_id_candidate_capability_profiles",
            type_="foreignkey",
        )
        batch.drop_constraint(
            foreign_keys.get(("evidence_snapshot_id",))
            or "fk_score_snapshots_evidence_snapshot_id_candidate_evidence_snapshots",
            type_="foreignkey",
        )
        batch.drop_constraint(
            foreign_keys.get(("previous_snapshot_id",))
            or "fk_score_snapshots_previous_snapshot_id_score_snapshots",
            type_="foreignkey",
        )
        batch.drop_constraint("uq_score_application_version", type_="unique")
        batch.create_unique_constraint("uq_score_application_stage", ["application_id", "stage"])
        batch.drop_column("capability_profile_id")
        batch.drop_column("evidence_snapshot_id")
        batch.drop_column("previous_snapshot_id")
        batch.drop_column("version")