"""Initial tenant roots for parallel backend development."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "organizations",
        sa.Column("id", uid, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "users",
        sa.Column("id", uid, primary_key=True),
        sa.Column("normalized_email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("is_superadmin", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "widgets",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("public_key", sa.String(128), unique=True, nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("greeting", sa.Text, nullable=False),
        sa.Column("allowed_origins", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_table(
        "customers",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("external_id", sa.Text),
        sa.Column("email", sa.String(320)),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_index(
        "uq_customers_tenant_external",
        "customers",
        ["tenant_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )


def downgrade():
    for name in ("jobs", "customers", "widgets", "users", "organizations"):
        op.drop_table(name)
