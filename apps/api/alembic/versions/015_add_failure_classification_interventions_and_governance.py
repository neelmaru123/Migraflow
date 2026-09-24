"""add_failure_classification_interventions_and_governance

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-24 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Add approval governance columns to migration_plans
    op.add_column('migration_plans', sa.Column('approved_version_number', sa.Integer(), nullable=True))
    op.add_column('migration_plans', sa.Column('approved_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    op.add_column('migration_plans', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))

    # 2. Add approval governance columns to migration_plan_versions
    op.add_column('migration_plan_versions', sa.Column('is_approved', sa.Boolean(), server_default='false', nullable=True))
    op.add_column('migration_plan_versions', sa.Column('approved_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    op.add_column('migration_plan_versions', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))

    # 3. Add replan and recovery counters to migration_execution_plans
    op.add_column('migration_execution_plans', sa.Column('replan_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('migration_execution_plans', sa.Column('recovery_count', sa.Integer(), server_default='0', nullable=False))

    # 4. Add replan, recovery, and failure category fields to migration_execution_steps
    op.add_column('migration_execution_steps', sa.Column('replan_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('migration_execution_steps', sa.Column('recovery_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('migration_execution_steps', sa.Column('failure_category', sa.String(length=100), nullable=True))
    op.add_column('migration_execution_steps', sa.Column('failure_code', sa.String(length=100), nullable=True))

    # 5. Create user_interventions table
    op.create_table(
        'user_interventions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('migration_job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_jobs.id', ondelete='CASCADE'), index=True, nullable=False),
        sa.Column('step_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_execution_steps.id', ondelete='SET NULL'), index=True, nullable=True),
        sa.Column('failure_category', sa.String(length=100), nullable=False),
        sa.Column('failure_code', sa.String(length=100), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('suggested_action', sa.String(length=100), nullable=True),
        sa.Column('options', json_type, nullable=False),
        sa.Column('context_data', json_type, nullable=True),
        sa.Column('status', sa.String(length=50), server_default='pending', index=True, nullable=False),
        sa.Column('user_response', json_type, nullable=True),
        sa.Column('resolved_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('user_interventions')
    op.drop_column('migration_execution_steps', 'failure_code')
    op.drop_column('migration_execution_steps', 'failure_category')
    op.drop_column('migration_execution_steps', 'recovery_count')
    op.drop_column('migration_execution_steps', 'replan_count')
    op.drop_column('migration_execution_plans', 'recovery_count')
    op.drop_column('migration_execution_plans', 'replan_count')
    op.drop_column('migration_plan_versions', 'approved_at')
    op.drop_column('migration_plan_versions', 'approved_by_user_id')
    op.drop_column('migration_plan_versions', 'is_approved')
    op.drop_column('migration_plans', 'approved_at')
    op.drop_column('migration_plans', 'approved_by_user_id')
    op.drop_column('migration_plans', 'approved_version_number')
