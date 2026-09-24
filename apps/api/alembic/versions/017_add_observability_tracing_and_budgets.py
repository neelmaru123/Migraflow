"""add_observability_tracing_and_budgets

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-24 17:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Create execution_traces table
    op.create_table(
        'execution_traces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('trace_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('parent_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('execution_traces.id', ondelete='CASCADE'), nullable=True),
        sa.Column('migration_job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_jobs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('agent_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agent_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('execution_step_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_execution_steps.id', ondelete='SET NULL'), nullable=True),
        sa.Column('operation_type', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), server_default='running', nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True),
        sa.Column('error_type', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata_snapshot', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_execution_traces_trace_id', 'execution_traces', ['trace_id'])
    op.create_index('ix_execution_traces_parent_run_id', 'execution_traces', ['parent_run_id'])
    op.create_index('ix_execution_traces_job_id', 'execution_traces', ['migration_job_id'])
    op.create_index('ix_execution_traces_agent_run_id', 'execution_traces', ['agent_run_id'])
    op.create_index('ix_execution_traces_step_id', 'execution_traces', ['execution_step_id'])
    op.create_index('ix_execution_traces_operation_type', 'execution_traces', ['operation_type'])
    op.create_index('ix_execution_traces_status', 'execution_traces', ['status'])

    # 2. Create llm_call_records table
    op.create_table(
        'llm_call_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('trace_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('execution_trace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('execution_traces.id', ondelete='SET NULL'), nullable=True),
        sa.Column('migration_plan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_plans.id', ondelete='SET NULL'), nullable=True),
        sa.Column('migration_job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('model_version', sa.String(length=50), nullable=True),
        sa.Column('prompt_version', sa.String(length=50), nullable=False),
        sa.Column('planner_version', sa.String(length=50), server_default='migraflow-planner-v2.0', nullable=False),
        sa.Column('schema_version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('request_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('latency_ms', sa.Float(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('estimated_cost_usd', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=50), server_default='success', nullable=False),
        sa.Column('structured_output_valid', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('validation_error', sa.Text(), nullable=True),
        sa.Column('sanitized_prompt_preview', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_llm_call_records_trace_id', 'llm_call_records', ['trace_id'])
    op.create_index('ix_llm_call_records_execution_trace_id', 'llm_call_records', ['execution_trace_id'])
    op.create_index('ix_llm_call_records_plan_id', 'llm_call_records', ['migration_plan_id'])
    op.create_index('ix_llm_call_records_job_id', 'llm_call_records', ['migration_job_id'])
    op.create_index('ix_llm_call_records_status', 'llm_call_records', ['status'])

    # 3. Create resource_budgets table
    op.create_table(
        'resource_budgets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('migration_job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('migration_jobs.id', ondelete='CASCADE'), unique=True, nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('max_llm_calls', sa.Integer(), server_default='10', nullable=False),
        sa.Column('max_replans', sa.Integer(), server_default='3', nullable=False),
        sa.Column('max_retries', sa.Integer(), server_default='3', nullable=False),
        sa.Column('max_execution_duration_seconds', sa.Integer(), server_default='3600', nullable=False),
        sa.Column('max_concurrent_steps', sa.Integer(), server_default='4', nullable=False),
        sa.Column('max_tokens', sa.BigInteger(), server_default='100000', nullable=False),
        sa.Column('max_cost_usd', sa.Float(), server_default='5.0', nullable=False),
        sa.Column('current_llm_calls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('current_replans', sa.Integer(), server_default='0', nullable=False),
        sa.Column('current_retries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('current_tokens', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('current_cost_usd', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('is_exceeded', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('exceeded_limit_type', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_resource_budgets_job_id', 'resource_budgets', ['migration_job_id'])
    op.create_index('ix_resource_budgets_user_id', 'resource_budgets', ['user_id'])


def downgrade() -> None:
    op.drop_table('resource_budgets')
    op.drop_table('llm_call_records')
    op.drop_table('execution_traces')
