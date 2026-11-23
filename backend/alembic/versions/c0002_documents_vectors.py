"""Tenant-scoped image attachments, document import parts and exact vectors."""

from alembic import op

revision = "c0002_documents_vectors"
down_revision = "b0006_widget_sections"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE knowledge_imports DROP CONSTRAINT knowledge_imports_state_check")
    op.execute(
        "ALTER TABLE knowledge_imports ADD CONSTRAINT knowledge_imports_state_check CHECK (state IN ('queued','processing','ready','failed'))"
    )
    op.execute("""CREATE TABLE knowledge_import_files (
        tenant_id uuid NOT NULL, import_id uuid NOT NULL, actor_id uuid NOT NULL,
        filename text NOT NULL, format text NOT NULL CHECK (format IN ('pdf','docx')),
        content bytea NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 10485760),
        PRIMARY KEY (tenant_id,import_id),
        FOREIGN KEY (tenant_id,import_id) REFERENCES knowledge_imports(tenant_id,id) ON DELETE CASCADE
    )""")
    op.execute("""CREATE TABLE attachments (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, conversation_id uuid NOT NULL,
        customer_id uuid NOT NULL, widget_id uuid NOT NULL,
        filename text NOT NULL, mime text NOT NULL CHECK (mime IN ('image/jpeg','image/png','image/webp')),
        size integer NOT NULL CHECK (size BETWEEN 1 AND 5242880),
        state text NOT NULL CHECK (state IN ('pending','ready','failed')),
        content bytea NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, conversation_id, id),
        FOREIGN KEY (tenant_id,conversation_id) REFERENCES conversations(tenant_id,id),
        FOREIGN KEY (tenant_id,customer_id) REFERENCES customers(tenant_id,id),
        FOREIGN KEY (tenant_id,widget_id) REFERENCES widgets(tenant_id,id),
        CHECK (octet_length(content)=size)
    )""")
    op.execute("""CREATE TABLE message_attachments (
        tenant_id uuid NOT NULL, conversation_id uuid NOT NULL, message_id uuid NOT NULL,
        attachment_id uuid NOT NULL, position integer NOT NULL CHECK (position BETWEEN 0 AND 3),
        PRIMARY KEY (tenant_id,conversation_id,message_id,attachment_id),
        UNIQUE (tenant_id,conversation_id,message_id,position),
        FOREIGN KEY (tenant_id,conversation_id,message_id) REFERENCES messages(tenant_id,conversation_id,id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id,conversation_id,attachment_id) REFERENCES attachments(tenant_id,conversation_id,id)
    )""")
    op.execute("""CREATE TABLE knowledge_import_articles (
        tenant_id uuid NOT NULL, import_id uuid NOT NULL, article_id uuid NOT NULL,
        position integer NOT NULL CHECK (position>=0),
        PRIMARY KEY (tenant_id,import_id,position),
        FOREIGN KEY (tenant_id,import_id) REFERENCES knowledge_imports(tenant_id,id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id,article_id) REFERENCES articles(tenant_id,id)
    )""")
    op.execute("""CREATE TABLE knowledge_chunks (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, article_id uuid NOT NULL,
        revision_id uuid NOT NULL, position integer NOT NULL CHECK (position>=0),
        content text NOT NULL CHECK (length(content) BETWEEN 1 AND 2000),
        embedding vector NOT NULL, embedding_model text NOT NULL,
        embedding_dimension integer NOT NULL CHECK (embedding_dimension BETWEEN 1 AND 2000),
        CHECK (vector_dims(embedding)=embedding_dimension),
        UNIQUE (tenant_id,article_id,revision_id,position,embedding_model),
        FOREIGN KEY (tenant_id,article_id,revision_id) REFERENCES article_revisions(tenant_id,article_id,id)
    )""")
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_scope ON knowledge_chunks (tenant_id,embedding_model,embedding_dimension)"
    )


def downgrade():
    op.execute("DROP TABLE knowledge_chunks")
    op.execute("DROP TABLE knowledge_import_articles")
    op.execute("DROP TABLE knowledge_import_files")
    op.execute("ALTER TABLE knowledge_imports DROP CONSTRAINT knowledge_imports_state_check")
    op.execute(
        "ALTER TABLE knowledge_imports ADD CONSTRAINT knowledge_imports_state_check CHECK (state IN ('ready','failed'))"
    )
    op.execute("DROP TABLE message_attachments")
    op.execute("DROP TABLE attachments")
