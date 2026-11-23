"""Bind widgets to knowledge sections within a tenant."""

from alembic import op

revision = "b0006_widget_sections"
down_revision = "b0005_merge_c02_c03"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE widget_sections (
            tenant_id uuid NOT NULL,
            widget_id uuid NOT NULL,
            section_id uuid NOT NULL,
            PRIMARY KEY (tenant_id, widget_id, section_id),
            FOREIGN KEY (tenant_id, widget_id) REFERENCES widgets(tenant_id, id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id, section_id) REFERENCES article_sections(tenant_id, id)
        )
    """)


def downgrade():
    op.execute("DROP TABLE widget_sections")
