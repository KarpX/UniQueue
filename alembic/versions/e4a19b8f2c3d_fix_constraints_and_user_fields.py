"""fix constraints and user fields

Revision ID: e4a19b8f2c3d
Revises: 186002678e4e
Create Date: 2026-10-12 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a19b8f2c3d'
down_revision: Union[str, Sequence[str], None] = '186002678e4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add first_name and last_name to users
    op.add_column('users', sa.Column('first_name', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('last_name', sa.String(length=255), nullable=True))

    # 2. Add expires_at to swap_requests
    op.add_column('swap_requests', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))

    # 3. Clean up any duplicates in queue_entries before adding unique constraint
    op.execute(
        """
        DELETE FROM queue_entries a
        USING queue_entries b
        WHERE a.id < b.id
          AND a.queue_id = b.queue_id
          AND a.user_id = b.user_id;
        """
    )

    # 4. Create unique constraint on queue_entries (queue_id, user_id)
    op.create_unique_constraint('uq_queue_entries_queue_user', 'queue_entries', ['queue_id', 'user_id'])


def downgrade() -> None:
    op.drop_constraint('uq_queue_entries_queue_user', 'queue_entries', type_='unique')
    op.drop_column('swap_requests', 'expires_at')
    op.drop_column('users', 'last_name')
    op.drop_column('users', 'first_name')
