"""Shared bounded-rate counters for anonymous HTTP entry points."""

from alembic import op

revision = "b0008_request_limits"
down_revision = "b0007_limits_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE request_limits (bucket_key text PRIMARY KEY, window_start timestamptz NOT NULL, attempts integer NOT NULL CHECK (attempts > 0))"
    )


def downgrade():
    op.execute("DROP TABLE request_limits")
