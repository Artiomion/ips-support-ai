"""Widget sessions, one-time identity nonces, invitations and categories."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b0003_platform"
down_revision = "b0002_auth"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "customer_sessions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, nullable=False),
        sa.Column("customer_id", uid, nullable=False),
        sa.Column("widget_id", uid, nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("site_origin", sa.Text, nullable=False),
        sa.Column("conversation_scope", uid, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "widget_id"], ["widgets.tenant_id", "widgets.id"]),
    )
    op.create_table(
        "identity_nonces",
        sa.Column("tenant_id", uid, nullable=False),
        sa.Column("widget_id", uid, nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "widget_id", "nonce_hash"),
        sa.ForeignKeyConstraint(["tenant_id", "widget_id"], ["widgets.tenant_id", "widgets.id"]),
    )
    op.create_table(
        "categories",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    op.create_table(
        "specialist_categories",
        sa.Column("tenant_id", uid, nullable=False),
        sa.Column("membership_id", uid, nullable=False),
        sa.Column("category_id", uid, nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "membership_id", "category_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"], ["categories.tenant_id", "categories.id"]
        ),
    )
    op.create_table(
        "invitations",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('admin', 'specialist')", name="ck_invitation_role"),
    )


def downgrade():
    for table in (
        "invitations",
        "specialist_categories",
        "categories",
        "identity_nonces",
        "customer_sessions",
    ):
        op.drop_table(table)
