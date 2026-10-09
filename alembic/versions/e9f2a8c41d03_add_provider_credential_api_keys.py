"""add api_keys pool to provider_credential for Agnes batch

Revision ID: e9f2a8c41d03
Revises: c8e4a1b92f70
Create Date: 2026-09-09 14:55:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9f2a8c41d03"
down_revision: str | Sequence[str] | None = "c8e4a1b92f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("provider_credential", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_keys", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("provider_credential", schema=None) as batch_op:
        batch_op.drop_column("api_keys")
