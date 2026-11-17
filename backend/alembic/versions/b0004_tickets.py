"""Tenant-scoped tickets; conversation FK is added after C02 exists."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b0004_tickets"
down_revision = "b0003_platform"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "tickets",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("conversation_id", uid, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("category_id", uid),
        sa.Column("assignee_id", uid),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("first_staff_reply_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"], ["categories.tenant_id", "categories.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assignee_id"], ["memberships.tenant_id", "memberships.id"]
        ),
        sa.CheckConstraint(
            "status IN ('new','waiting_specialist','in_progress','waiting_customer','resolved','closed')",
            name="ck_ticket_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','urgent')", name="ck_ticket_priority"
        ),
        sa.CheckConstraint("version >= 1", name="ck_ticket_version"),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_index(
        "uq_active_ticket_conversation",
        "tickets",
        ["tenant_id", "conversation_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('resolved', 'closed')"),
    )


def downgrade():
    op.drop_index("uq_active_ticket_conversation", table_name="tickets")
    op.drop_table("tickets")
