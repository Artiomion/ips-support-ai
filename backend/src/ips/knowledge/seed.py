"""Optional, tenant-scoped demonstration knowledge; never runs automatically."""

from sqlalchemy import text

from ips.knowledge.repository import KnowledgeRepository
from ips.knowledge.schemas import ArticleInput


def seed_demo_knowledge(db, tenant_id, actor_id):
    """Idempotently add sample public/internal articles to one existing tenant."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"demo-knowledge:{tenant_id}"},
    )
    repo = KnowledgeRepository()
    section = repo.create_section(db, tenant_id, "Demo support")
    if section is None:
        section = (
            db.execute(
                text("SELECT id,name FROM article_sections WHERE tenant_id=:tenant AND name=:name"),
                {"tenant": tenant_id, "name": "Demo support"},
            )
            .mappings()
            .one()
        )
    result = []
    for title, body, visibility in (
        ("Demo contact", "Contact our support team for help with your account.", "public"),
        (
            "Demo staff notes",
            "Internal sample: escalate requests requiring staff review.",
            "internal",
        ),
    ):
        existing = db.execute(
            text("""SELECT a.id FROM articles a JOIN article_revisions r
            ON r.tenant_id=a.tenant_id AND r.article_id=a.id AND r.id=a.current_revision_id
            WHERE a.tenant_id=:tenant AND a.section_id=:section AND r.title=:title LIMIT 1"""),
            {"tenant": tenant_id, "section": section["id"], "title": title},
        ).scalar_one_or_none()
        if existing is None:
            created = repo.create_article(
                db,
                tenant_id,
                actor_id,
                ArticleInput(
                    section_id=section["id"],
                    title=title,
                    body=body,
                    visibility=visibility,
                    status="published",
                ),
            )
            existing = created["id"]
        result.append(existing)
    return result
