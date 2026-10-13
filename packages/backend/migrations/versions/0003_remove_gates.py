"""Remove the Gate 1 / Gate 2 approvals and pipeline execution: the pipeline
now stops at the coverage report, and test cases are approved and run the
usual way.

Revision ID: 0003_remove_gates
Revises: 0002_requirements
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_remove_gates"
down_revision = "0002_requirements"
branch_labels = None
depends_on = None


def upgrade() -> None:
  # Requirements that were waiting at a gate or running tests are finished
  # as far as the new pipeline is concerned.
  op.execute(
    "UPDATE \"Requirement\" SET status = 'completed' "
    "WHERE status IN ('awaiting_gate1', 'awaiting_gate2', 'executing', 'changes_requested')"
  )
  op.drop_index("GateDecision_requirementId_idx", table_name="GateDecision")
  op.drop_table("GateDecision")
  op.drop_column("Requirement", "executionBatchId")


def downgrade() -> None:
  op.add_column("Requirement", sa.Column("executionBatchId", sa.Text(), nullable=True))
  op.create_table(
    "GateDecision",
    sa.Column("id", sa.Text(), nullable=False),
    sa.Column("requirementId", sa.Text(), nullable=False),
    sa.Column("gate", sa.Integer(), nullable=False),
    sa.Column("decision", sa.Text(), nullable=False),
    sa.Column("note", sa.Text(), nullable=True),
    sa.Column("userId", sa.Text(), nullable=True),
    sa.Column("createdAt", postgresql.TIMESTAMP(precision=3), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    sa.ForeignKeyConstraint(["requirementId"], ["Requirement.id"], onupdate="CASCADE", ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["userId"], ["User.id"], onupdate="CASCADE", ondelete="SET NULL"),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index("GateDecision_requirementId_idx", "GateDecision", ["requirementId"], unique=False)
