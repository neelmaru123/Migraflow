"""add_agent_runs_and_execution_events

Revision ID: d1e2f3a4b5c6
Revises: c9f0a2b3456e
Create Date: 2026-09-24 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c9f0a2b3456e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Add current_run_id and idempotency_key to migration_jobs
    op.add_column(
        'migration_jobs',
        sa.Column(
            'current_run_id',
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Pointer to the currently active AgentRun attempt for this migration job.",
        ),
    )
    op.add_column(
        'migration_jobs',
        sa.Column(
            'idempotency_key',
            sa.String(255),
            nullable=True,
            comment="Client-provided idempotency key preventing duplicate migration submissions.",
        ),
    )
    op.create_index(
        'idx_migration_jobs_current_run_id',
        'migration_jobs',
        ['current_run_id'],
    )
    op.create_index(
        'idx_migration_jobs_idempotency_key',
        'migration_jobs',
        ['idempotency_key'],
        unique=True,
    )

    # 2. Create agent_runs table
    op.create_table(
        'agent_runs',
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
            index=True,
        ),
        sa.Column(
            'agent_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('agents.id', ondelete='SET NULL'),
            nullable=True,
            index=True,
        ),
        sa.Column(
            'status',
            sa.String(50),
            nullable=False,
            server_default='preparing',
            index=True,
        ),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('agent_version', sa.String(50), nullable=True),
        sa.Column('execution_engine_version', sa.String(50), nullable=True),
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
    )

    # 3. Create execution_events table
    op.create_table(
        'execution_events',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'event_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            'agent_run_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('agent_runs.id', ondelete='SET NULL'),
            nullable=True,
            index=True,
        ),
        sa.Column(
            'migration_job_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('migration_jobs.id', ondelete='CASCADE'),
            nullable=True,
            index=True,
        ),
        sa.Column(
            'migration_plan_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('migration_plans.id', ondelete='CASCADE'),
            nullable=True,
            index=True,
        ),
        sa.Column('event_type', sa.String(100), nullable=False, index=True),
        sa.Column('actor_type', sa.String(50), nullable=False, server_default='system'),
        sa.Column('actor_id', sa.String(255), nullable=True),
        sa.Column(
            'timestamp',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
            index=True,
        ),
        sa.Column('payload', json_type, nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    op.drop_table('execution_events')
    op.drop_table('agent_runs')
    op.drop_index('idx_migration_jobs_idempotency_key', table_name='migration_jobs')
    op.drop_index('idx_migration_jobs_current_run_id', table_name='migration_jobs')
    op.drop_column('migration_jobs', 'idempotency_key')
    op.drop_column('migration_jobs', 'current_run_id')
