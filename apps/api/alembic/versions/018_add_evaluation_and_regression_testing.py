"""add_evaluation_and_regression_testing

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-09-25 11:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Create evaluation_suite_runs table
    op.create_table(
        'evaluation_suite_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('suite_name', sa.String(length=100), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('model_version', sa.String(length=50), nullable=True),
        sa.Column('prompt_version', sa.String(length=50), nullable=False),
        sa.Column('planner_version', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), server_default='pending', nullable=False),
        sa.Column('total_scenarios', sa.Integer(), server_default='15', nullable=False),
        sa.Column('passed_scenarios', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_scenarios', sa.Integer(), server_default='0', nullable=False),
        sa.Column('pass_rate', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('average_planning_score', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('recovery_routing_accuracy', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('llm_output_valid_rate', sa.Float(), server_default='1.0', nullable=False),
        sa.Column('total_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_llm_calls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_cost_usd', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('total_duration_ms', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('quality_gate_passed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('quality_gate_details', json_type, nullable=False),
        sa.Column('summary_report', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_evaluation_suite_runs_status', 'evaluation_suite_runs', ['status'])

    # 2. Create evaluation_scenario_results table
    op.create_table(
        'evaluation_scenario_results',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('suite_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('evaluation_suite_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scenario_id', sa.String(length=100), nullable=False),
        sa.Column('scenario_name', sa.String(length=255), nullable=False),
        sa.Column('scenario_category', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('planning_metrics', json_type, nullable=False),
        sa.Column('recovery_metrics', json_type, nullable=False),
        sa.Column('llm_metrics', json_type, nullable=False),
        sa.Column('cost_latency_metrics', json_type, nullable=False),
        sa.Column('failure_injection_metrics', json_type, nullable=False),
        sa.Column('errors', json_type, nullable=False),
        sa.Column('warnings', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_evaluation_scenario_results_suite_run_id', 'evaluation_scenario_results', ['suite_run_id'])
    op.create_index('ix_evaluation_scenario_results_scenario_id', 'evaluation_scenario_results', ['scenario_id'])


def downgrade() -> None:
    op.drop_index('ix_evaluation_scenario_results_scenario_id', table_name='evaluation_scenario_results')
    op.drop_index('ix_evaluation_scenario_results_suite_run_id', table_name='evaluation_scenario_results')
    op.drop_table('evaluation_scenario_results')

    op.drop_index('ix_evaluation_suite_runs_status', table_name='evaluation_suite_runs')
    op.drop_table('evaluation_suite_runs')
