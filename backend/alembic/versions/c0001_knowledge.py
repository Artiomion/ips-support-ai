"""Tenant-scoped knowledge sections, articles, revisions and imports."""

from alembic import op

revision = "c0001_knowledge"
down_revision = "b0004_tickets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE article_sections (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES organizations(id),
            name varchar(120) NOT NULL,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, name)
        )
    """)
    op.execute("""
        CREATE TABLE articles (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES organizations(id),
            section_id uuid NOT NULL,
            visibility text NOT NULL CHECK (visibility IN ('public', 'internal')),
            status text NOT NULL CHECK (status IN ('draft', 'published', 'disabled')),
            current_revision_id uuid,
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, section_id) REFERENCES article_sections(tenant_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE article_revisions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            article_id uuid NOT NULL,
            version integer NOT NULL CHECK (version >= 1),
            title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            body text NOT NULL CHECK (length(body) BETWEEN 1 AND 100000),
            source_type text NOT NULL,
            source_id uuid,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, article_id, id),
            UNIQUE (tenant_id, article_id, version),
            FOREIGN KEY (tenant_id, article_id) REFERENCES articles(tenant_id, id)
        )
    """)
    op.execute("""
        ALTER TABLE articles ADD CONSTRAINT fk_articles_current_revision
        FOREIGN KEY (tenant_id, id, current_revision_id)
        REFERENCES article_revisions (tenant_id, article_id, id)
    """)
    op.execute("""
        CREATE TABLE knowledge_imports (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES organizations(id),
            section_id uuid NOT NULL,
            state text NOT NULL CHECK (state IN ('ready', 'failed')),
            error_code text,
            article_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, section_id) REFERENCES article_sections(tenant_id, id),
            FOREIGN KEY (tenant_id, article_id) REFERENCES articles(tenant_id, id)
        )
    """)
    op.execute("CREATE INDEX ix_articles_tenant_status ON articles (tenant_id, status, id)")


def downgrade():
    op.execute("DROP TABLE knowledge_imports")
    op.execute("ALTER TABLE articles DROP CONSTRAINT fk_articles_current_revision")
    op.execute("DROP TABLE article_revisions")
    op.execute("DROP TABLE articles")
    op.execute("DROP TABLE article_sections")
