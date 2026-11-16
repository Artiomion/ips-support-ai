"""Memberships and server-side staff sessions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b0002_auth"
down_revision = "b0001"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "memberships",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", uid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("available", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("max_active", sa.Integer, nullable=False, server_default="5"),
        sa.UniqueConstraint("tenant_id", "user_id"),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.CheckConstraint("role IN ('admin', 'specialist')", name="ck_membership_role"),
        sa.CheckConstraint("max_active > 0", name="ck_membership_max_active"),
    )
    op.create_table(
        "staff_sessions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("active_tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("staff_sessions")
    op.drop_table("memberships")
