"""Store conversation link grants and customer ratings."""

from alembic import op

revision = "b0009_customer_followups"
down_revision = "b0008_request_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE customer_links (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            customer_id uuid NOT NULL,
            conversation_id uuid NOT NULL,
            purpose text NOT NULL CHECK (purpose IN ('confirm', 'exchange')),
            confirmation_id uuid REFERENCES customer_links(id),
            site_origin text NOT NULL,
            email text NOT NULL,
            token_hash varchar(64) NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 minutes'),
            consumed_at timestamptz,
            CHECK ((purpose = 'exchange') = (confirmation_id IS NOT NULL)),
            FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id),
            FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_customer_links_confirmed ON customer_links
            (tenant_id, conversation_id, consumed_at DESC, id DESC)
        WHERE purpose = 'confirm' AND consumed_at IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE ratings (
            ticket_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            customer_id uuid NOT NULL,
            score integer NOT NULL CHECK (score BETWEEN 1 AND 5),
            comment varchar(2000),
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, ticket_id) REFERENCES tickets(tenant_id, id),
            FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id)
        )
    """)


def downgrade():
    op.execute("DROP TABLE ratings")
    op.execute("DROP TABLE customer_links")
