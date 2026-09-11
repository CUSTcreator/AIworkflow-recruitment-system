"""complete step runtime contract
Revision ID: 055_step_runtime_done
Revises: 054_workflow_artifact_v2
"""
from alembic import op
import sqlalchemy as sa
revision="055_step_runtime_done"
down_revision="054_workflow_artifact_v2"
branch_labels=None
depends_on=None
def upgrade():
 with op.batch_alter_table("workflow_step_checkpoints") as b:
  b.add_column(sa.Column("external_request_id",sa.String(length=128),nullable=True))
  b.add_column(sa.Column("lease_owner",sa.String(length=128),nullable=True))
  b.add_column(sa.Column("lease_expires_at",sa.DateTime(),nullable=True))
  b.create_index("ix_workflow_step_checkpoint_lease",["status","lease_expires_at"])
  b.create_index("ix_workflow_step_checkpoint_request",["external_request_id"])
 op.execute("UPDATE workflow_runs SET definition_version=0, error_message='workflow_step_runtime_migration_requires_restart' WHERE status IN ('pending','running')")
def downgrade():
 with op.batch_alter_table("workflow_step_checkpoints") as b:
  b.drop_index("ix_workflow_step_checkpoint_request"); b.drop_index("ix_workflow_step_checkpoint_lease")
  b.drop_column("lease_expires_at"); b.drop_column("lease_owner"); b.drop_column("external_request_id")