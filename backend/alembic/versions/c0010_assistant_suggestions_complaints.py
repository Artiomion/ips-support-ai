"""C10 staff assistant, manually reviewed suggestions and complaints."""

from alembic import op

revision = "c0010_assistant"
down_revision = "b0006_widget_sections"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE staff_assistant_messages (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
        staff_id uuid NOT NULL, question text NOT NULL, answer text NOT NULL,
        sources jsonb NOT NULL DEFAULT '[]'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id,ticket_id) REFERENCES tickets(tenant_id,id))""")
    op.execute("""CREATE TABLE knowledge_suggestions (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
        target_article_id uuid, draft_text text NOT NULL, fingerprint text NOT NULL,
        state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','approved','rejected')),
        pii_flags jsonb NOT NULL DEFAULT '[]'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id,id), UNIQUE (tenant_id,ticket_id,fingerprint),
        FOREIGN KEY (tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
        FOREIGN KEY (tenant_id,target_article_id) REFERENCES articles(tenant_id,id))""")
    op.execute("""CREATE TABLE ai_complaints (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, conversation_id uuid NOT NULL,
        message_id uuid NOT NULL, reason text NOT NULL,
        status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','reviewed')),
        source_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb, admin_note text,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id,id),
        FOREIGN KEY (tenant_id,conversation_id,message_id)
            REFERENCES messages(tenant_id,conversation_id,id))""")
    op.execute("CREATE INDEX ix_suggestions_tenant ON knowledge_suggestions(tenant_id,id)")
    op.execute("CREATE INDEX ix_complaints_tenant ON ai_complaints(tenant_id,id)")


def downgrade():
    op.execute("DROP TABLE ai_complaints")
    op.execute("DROP TABLE knowledge_suggestions")
    op.execute("DROP TABLE staff_assistant_messages")
