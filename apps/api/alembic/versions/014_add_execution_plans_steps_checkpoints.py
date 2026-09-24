"""add_execution_plans_steps_checkpoints

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-24 13:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Create migration_execution_plans table
    op.create_table(
        'migration_execution_plans',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'migration_job_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('migration_jobs.id', ondelete='CASCADE'),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            'migration_plan_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('migration_plans.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        ),
        sa.Column(
            'migration_plan_version_id',
            postgresql.UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column(
            'status',
            sa.String(50),
            nullable=False,
            server_default='pending',
            index=True,
        ),
        sa.Column(
            'concurrency_limit',
            sa.Integer(),
            nullable=False,
            server_default='2',
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Create migration_execution_steps table
    op.create_table(
        'migration_execution_steps',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'execution_plan_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('migration_execution_plans.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        ),
        sa.Column('step_key', sa.String(100), nullable=False, index=True),
        sa.Column('step_type', sa.String(50), nullable=False, index=True),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('dependencies', json_type, nullable=False, server_default='[]'),
        sa.Column(
            'status',
            sa.String(50),
            nullable=False,
            server_default='pending',
            index=True,
        ),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'agent_run_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('agent_runs.id', ondelete='SET NULL'),
            nullable=True,
            index=True,
        ),
        sa.Column('input_definition', json_type, nullable=False, server_default='{}'),
        sa.Column('output_summary', json_type, nullable=False, server_default='{}'),
        sa.Column('error_type', sa.String(100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.UniqueConstraint(
            'execution_plan_id',
            'step_key',
            name='uq_execution_steps_plan_step_key',
        ),
    )

    # 3. Create execution_checkpoints table
    op.create_table(
        'execution_checkpoints',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'execution_step_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('migration_execution_steps.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        ),
        sa.Column('source_identifier', sa.String(100), nullable=False, server_default='default'),
        sa.Column('source_table', sa.String(255), nullable=False),
        sa.Column('target_table', sa.String(255), nullable=False),
        sa.Column('cursor_offset', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('rows_processed', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('source_position', json_type, nullable=True),
        sa.Column('checkpoint_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.UniqueConstraint(
            'execution_step_id',
            'source_identifier',
            'source_table',
            'target_table',
            name='uq_checkpoint_step_source_target',
        ),
    )


def downgrade() -> None:
    op.drop_table('execution_checkpoints')
    op.drop_table('migration_execution_steps')
    op.drop_table('migration_execution_plans')
