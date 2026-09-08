"""add agent credential api_keys pool

Revision ID: c8e4a1b92f70
Revises: b3f9c07ae214
Create Date: 2026-09-05 08:00:00.000000

"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e4a1b92f70"
down_revision: str | Sequence[str] | None = "b3f9c07ae214"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_keys", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("key_pool_state", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, api_key FROM agent_anthropic_credentials")).fetchall()
    for row in rows:
        key = (row.api_key or "").strip()
        if not key:
            continue
        payload = json.dumps([{"key": key}], ensure_ascii=False)
        bind.execute(
            sa.text("UPDATE agent_anthropic_credentials SET api_keys = :payload WHERE id = :id"),
            {"payload": payload, "id": row.id},
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.drop_column("key_pool_state")
        batch_op.drop_column("api_keys")
