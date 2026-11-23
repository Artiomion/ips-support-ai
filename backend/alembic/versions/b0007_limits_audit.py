"""Atomic AI usage reservations and privacy-preserving audit events."""

from alembic import op

revision = "b0007_limits_audit"
down_revision = "b0006_widget_sections"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE usage_reservations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES organizations(id),
            run_id uuid NOT NULL,
            usage_day date NOT NULL,
            state text NOT NULL CHECK (state IN ('reserved','succeeded','failed','expired')),
            expires_at timestamptz NOT NULL,
            input_tokens integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            output_tokens integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            UNIQUE (tenant_id, run_id)
        )
    """)
    op.execute("CREATE INDEX ix_usage_tenant_day ON usage_reservations (tenant_id,usage_day,state)")
    op.execute("""
        CREATE TABLE audit_events (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            actor_id uuid NOT NULL,
            action text NOT NULL,
            object_id uuid,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_audit_tenant_id ON audit_events (tenant_id,id)")


def downgrade():
    op.execute("DROP TABLE audit_events")
    op.execute("DROP TABLE usage_reservations")
