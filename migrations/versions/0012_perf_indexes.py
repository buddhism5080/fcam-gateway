"""add useful indexes for logs / bindings / idempotency

Revision ID: 0012_perf_indexes
Revises: 0011_unified_pool_client_retries
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_perf_indexes"
down_revision = "0011_unified_pool_client_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Request log query paths: time range + client filter, request_id lookup
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])
    op.create_index("ix_request_logs_client_created", "request_logs", ["client_id", "created_at"])
    op.create_index("ix_request_logs_request_id", "request_logs", ["request_id"])

    # Credit refresh due-queue
    op.create_index("ix_api_keys_next_refresh_at", "api_keys", ["next_refresh_at"])
    op.create_index("ix_api_keys_status_active", "api_keys", ["is_active", "status"])

    # Resource sticky lookup already unique; index expires for cleanup
    op.create_index("ix_upstream_bindings_expires", "upstream_resource_bindings", ["expires_at"])

    # Idempotency expiry cleanup
    op.create_index("ix_idempotency_expires", "idempotency_records", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_expires", table_name="idempotency_records")
    op.drop_index("ix_upstream_bindings_expires", table_name="upstream_resource_bindings")
    op.drop_index("ix_api_keys_status_active", table_name="api_keys")
    op.drop_index("ix_api_keys_next_refresh_at", table_name="api_keys")
    op.drop_index("ix_request_logs_request_id", table_name="request_logs")
    op.drop_index("ix_request_logs_client_created", table_name="request_logs")
    op.drop_index("ix_request_logs_created_at", table_name="request_logs")
