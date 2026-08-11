"""unified pool: client max_retries; drop key daily-quota defaults

Revision ID: 0011_unified_pool_client_retries
Revises: 0010_add_provider_to_api_keys
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_unified_pool_client_retries"
down_revision = "0010_add_provider_to_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-downstream-key (client) upstream switch retry budget
    op.add_column(
        "clients",
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
    )

    # Keys no longer use daily quota for scheduling; keep columns for back-compat
    # but neutralize defaults so existing rows stop being quota-gated accidentally.
    try:
        op.execute(sa.text("UPDATE api_keys SET daily_quota = 0 WHERE daily_quota IS NOT NULL"))
    except Exception:
        pass

    # Detach keys from clients — unified global pool
    try:
        op.execute(sa.text("UPDATE api_keys SET client_id = NULL"))
    except Exception:
        pass


def downgrade() -> None:
    op.drop_column("clients", "max_retries")
