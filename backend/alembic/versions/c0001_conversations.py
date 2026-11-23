"""Tenant-scoped conversations, messages and pending AI runs."""

from alembic import op

revision = "c0001_conversations"
down_revision = "b0004_tickets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE conversations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES organizations(id),
            widget_id uuid NOT NULL,
            customer_id uuid NOT NULL,
            mode text NOT NULL DEFAULT 'bot' CHECK (mode IN ('bot', 'human')),
            outcome text NOT NULL DEFAULT 'unconfirmed'
                CHECK (outcome IN ('unconfirmed', 'ai_confirmed', 'human_confirmed')),
            version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, widget_id) REFERENCES widgets(tenant_id, id),
            FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_conversations_customer
        ON conversations (tenant_id, widget_id, customer_id, id)
    """)
    op.execute("""
        CREATE TABLE messages (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            conversation_id uuid NOT NULL,
            seq integer NOT NULL CHECK (seq >= 1),
            kind text NOT NULL CHECK (kind IN ('customer', 'assistant', 'specialist', 'internal_note')),
            author_id uuid,
            text text NOT NULL CHECK (length(text) BETWEEN 1 AND 8000),
            client_message_id uuid,
            sources jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, conversation_id, seq),
            UNIQUE (tenant_id, conversation_id, client_message_id),
            UNIQUE (tenant_id, conversation_id, id),
            FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE ai_runs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            conversation_id uuid NOT NULL,
            input_message_id uuid NOT NULL,
            state text NOT NULL DEFAULT 'pending',
            generation_version integer NOT NULL,
            provider text, model text, usage jsonb, error_code text,
            lease_until timestamptz, attempts integer NOT NULL DEFAULT 0,
            UNIQUE (tenant_id, input_message_id),
            FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id),
            FOREIGN KEY (tenant_id, conversation_id, input_message_id)
                REFERENCES messages(tenant_id, conversation_id, id)
        )
    """)
    op.execute("""
        ALTER TABLE tickets ADD CONSTRAINT fk_tickets_conversation
        FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
    """)
    op.execute("""
        ALTER TABLE customer_sessions ADD CONSTRAINT fk_customer_sessions_conversation
        FOREIGN KEY (tenant_id, conversation_scope) REFERENCES conversations(tenant_id, id)
    """)


def downgrade():
    op.execute("ALTER TABLE customer_sessions DROP CONSTRAINT fk_customer_sessions_conversation")
    op.execute("ALTER TABLE tickets DROP CONSTRAINT fk_tickets_conversation")
    op.execute("DROP TABLE ai_runs")
    op.execute("DROP TABLE messages")
    op.execute("DROP TABLE conversations")
