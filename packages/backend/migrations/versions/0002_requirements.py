"""Requirement pipeline: requirements, extracted criteria, test-case links,
coverage reports, Gate 1 / Gate 2 decisions and the pipeline timeline.

Revision ID: 0002_requirements
Revises: 0001_baseline
Create Date: 2026-10-12 16:51:55.146865
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002_requirements'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('Requirement',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('projectId', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('sourceText', sa.Text(), nullable=False),
    sa.Column('sourceType', sa.Text(), server_default=sa.text("'manual'"), nullable=False),
    sa.Column('sourceRef', sa.Text(), nullable=True),
    sa.Column('sourceUrl', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('failedStage', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('environmentId', sa.Text(), nullable=True),
    sa.Column('executionBatchId', sa.Text(), nullable=True),
    sa.Column('createdById', sa.Text(), nullable=True),
    sa.Column('createdAt', postgresql.TIMESTAMP(precision=3), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updatedAt', postgresql.TIMESTAMP(precision=3), nullable=False),
    sa.ForeignKeyConstraint(['createdById'], ['User.id'], onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['environmentId'], ['Environment.id'], onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['projectId'], ['Project.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('Requirement_projectId_idx', 'Requirement', ['projectId'], unique=False)
    op.create_table('CoverageReport',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('requirementId', sa.Text(), nullable=False),
    sa.Column('scorePercent', sa.Integer(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('criteria', sa.Text(), nullable=False),
    sa.Column('risks', sa.Text(), nullable=False),
    sa.Column('createdAt', postgresql.TIMESTAMP(precision=3), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['requirementId'], ['Requirement.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('CoverageReport_requirementId_idx', 'CoverageReport', ['requirementId'], unique=False)
    op.create_table('GateDecision',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('requirementId', sa.Text(), nullable=False),
    sa.Column('gate', sa.Integer(), nullable=False),
    sa.Column('decision', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('userId', sa.Text(), nullable=True),
    sa.Column('createdAt', postgresql.TIMESTAMP(precision=3), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['requirementId'], ['Requirement.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['userId'], ['User.id'], onupdate='CASCADE', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('GateDecision_requirementId_idx', 'GateDecision', ['requirementId'], unique=False)
    op.create_table('RequirementCriterion',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('requirementId', sa.Text(), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['requirementId'], ['Requirement.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('RequirementCriterion_requirementId_idx', 'RequirementCriterion', ['requirementId'], unique=False)
    op.create_table('RequirementEvent',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('requirementId', sa.Text(), nullable=False),
    sa.Column('stage', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('createdAt', postgresql.TIMESTAMP(precision=3), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['requirementId'], ['Requirement.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('RequirementEvent_requirementId_idx', 'RequirementEvent', ['requirementId'], unique=False)
    op.create_table('RequirementTestLink',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('requirementId', sa.Text(), nullable=False),
    sa.Column('criterionId', sa.Text(), nullable=True),
    sa.Column('testCaseId', sa.Text(), nullable=False),
    sa.Column('origin', sa.Text(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('createdAt', postgresql.TIMESTAMP(precision=3), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['criterionId'], ['RequirementCriterion.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requirementId'], ['Requirement.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['testCaseId'], ['TestCase.id'], onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('RequirementTestLink_requirementId_idx', 'RequirementTestLink', ['requirementId'], unique=False)
    op.create_index('RequirementTestLink_testCaseId_idx', 'RequirementTestLink', ['testCaseId'], unique=False)


def downgrade() -> None:
    op.drop_index('RequirementTestLink_testCaseId_idx', table_name='RequirementTestLink')
    op.drop_index('RequirementTestLink_requirementId_idx', table_name='RequirementTestLink')
    op.drop_table('RequirementTestLink')
    op.drop_index('RequirementEvent_requirementId_idx', table_name='RequirementEvent')
    op.drop_table('RequirementEvent')
    op.drop_index('RequirementCriterion_requirementId_idx', table_name='RequirementCriterion')
    op.drop_table('RequirementCriterion')
    op.drop_index('GateDecision_requirementId_idx', table_name='GateDecision')
    op.drop_table('GateDecision')
    op.drop_index('CoverageReport_requirementId_idx', table_name='CoverageReport')
    op.drop_table('CoverageReport')
    op.drop_index('Requirement_projectId_idx', table_name='Requirement')
    op.drop_table('Requirement')
