"""add_truncate_target_to_migration_jobs

Revision ID: c9f0a2b3456e
Revises: b8e9f1a2345d
Create Date: 2026-09-15 14:24:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9f0a2b3456e'
down_revision: Union[str, None] = 'b8e9f1a2345d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'migration_jobs',
        sa.Column(
            'truncate_target',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
            comment="Whether to perform a clean wipe (drop/truncate) on target tables before migration.",
        ),
    )


def downgrade() -> None:
    op.drop_column('migration_jobs', 'truncate_target')
