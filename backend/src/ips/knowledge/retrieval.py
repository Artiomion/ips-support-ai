"""Tenant and widget scoped PostgreSQL full-text search."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ips.core.ports import KnowledgeSnippet


class KnowledgeSearchFailure(Exception):
    """Search failed without exposing database details."""


class PostgresKnowledgeSearch:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def search(self, tenant_id, widget_id, query, audience, limit=5):
        if audience not in ("customer", "staff"):
            raise ValueError("Invalid audience")
        query = query.strip()[:4000]
        if not query or limit <= 0:
            return []
        # Until B adds widget_sections, no content is authorized for a widget.
        try:
            with self._session_factory() as db:
                if (
                    db.execute(
                        text("SELECT to_regclass('widget_sections') IS NOT NULL")
                    ).scalar_one()
                    is False
                ):
                    return []
                rows = (
                    db.execute(
                        text("""
                    SELECT a.id AS article_id, r.version AS revision, r.title,
                           left(ts_headline('simple', r.body, q,
                                'StartSel="", StopSel="", MaxWords=35, MinWords=15'), 2000) AS text
                    FROM articles a
                    JOIN article_revisions r ON r.tenant_id=a.tenant_id
                         AND r.article_id=a.id AND r.id=a.current_revision_id
                    JOIN widget_sections ws ON ws.tenant_id=a.tenant_id AND ws.section_id=a.section_id
                    JOIN widgets w ON w.tenant_id=ws.tenant_id AND w.id=ws.widget_id
                    CROSS JOIN plainto_tsquery('simple', :query) q
                    WHERE a.tenant_id=:tenant AND ws.widget_id=:widget AND w.enabled=true
                      AND a.visibility='public' AND a.status='published'
                      AND (setweight(to_tsvector('simple', r.title), 'A') ||
                           setweight(to_tsvector('simple', r.body), 'B')) @@ q
                    ORDER BY ts_rank(
                        setweight(to_tsvector('simple', r.title), 'A') ||
                        setweight(to_tsvector('simple', r.body), 'B'), q
                    ) DESC, a.id
                    LIMIT :limit
                """),
                        {
                            "tenant": tenant_id,
                            "widget": widget_id,
                            "query": query,
                            "limit": min(limit, 5),
                        },
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError:
            raise KnowledgeSearchFailure("search_unavailable") from None
        return [KnowledgeSnippet(**dict(row), visibility="public") for row in rows]
