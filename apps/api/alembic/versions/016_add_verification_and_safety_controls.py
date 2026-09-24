"""add_verification_and_safety_controls

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-09-24 16:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Create verification_results table
    op.create_table(
        'verification_results',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('migration_job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('execution_plan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_execution_plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('execution_step_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_execution_steps.id', ondelete='SET NULL'), nullable=True),
        sa.Column('check_type', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('target_table', sa.String(length=255), nullable=True),
        sa.Column('expected', json_type, nullable=True),
        sa.Column('actual', json_type, nullable=True),
        sa.Column('tolerance', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('details', json_type, nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_verification_results_job_id', 'verification_results', ['migration_job_id'])
    op.create_index('ix_verification_results_plan_id', 'verification_results', ['execution_plan_id'])
    op.create_index('ix_verification_results_step_id', 'verification_results', ['execution_step_id'])
    op.create_index('ix_verification_results_check_type', 'verification_results', ['check_type'])
    op.create_index('ix_verification_results_status', 'verification_results', ['status'])

    # 2. Create destructive_operation_approvals table
    op.create_table(
        'destructive_operation_approvals',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('migration_plan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_version_number', sa.Integer(), nullable=False),
        sa.Column('target_table', sa.String(length=255), nullable=False),
        sa.Column('operation_type', sa.String(length=100), nullable=False),
        sa.Column('risk_level', sa.String(length=50), server_default='destructive', nullable=False),
        sa.Column('approved_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('is_approved', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_valid', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('metadata_snapshot', json_type, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_destructive_approvals_plan_id', 'destructive_operation_approvals', ['migration_plan_id'])
    op.create_index('ix_destructive_approvals_version', 'destructive_operation_approvals', ['plan_version_number'])


def downgrade() -> None:
    op.drop_table('destructive_operation_approvals')
    op.drop_table('verification_results')
