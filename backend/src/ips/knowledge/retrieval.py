"""Tenant and widget scoped PostgreSQL full-text search."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ips.core.ports import KnowledgeSnippet
from ips.knowledge.embeddings import vector_literal


class KnowledgeSearchFailure(Exception):
    """Search failed without exposing database details."""


class PostgresKnowledgeSearch:
    def __init__(self, session_factory, embedding_provider=None):
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider

    def search(self, tenant_id, widget_id, query, audience, limit=5):
        if audience not in ("customer", "staff"):
            raise ValueError("Invalid audience")
        query = query.strip()[:4000]
        if not query or limit <= 0:
            return []
        if self._embedding_provider is not None:
            try:
                vector = self._embedding_provider.embed(query)
                return self.semantic_search(
                    tenant_id,
                    widget_id,
                    vector,
                    model=self._embedding_provider.model,
                    dimension=self._embedding_provider.dimension,
                    audience=audience,
                    limit=limit,
                )
            except Exception:  # noqa: BLE001 - avoid disclosing provider responses or credentials.
                raise KnowledgeSearchFailure("search_unavailable") from None
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

    def semantic_search(
        self, tenant_id, widget_id, query_vector, *, model, dimension, audience="customer", limit=5
    ):
        if audience not in ("customer", "staff"):
            raise ValueError("Invalid audience")
        if not isinstance(model, str) or not model.strip() or len(model) > 100:
            raise ValueError("Invalid embedding model")
        encoded = vector_literal(query_vector, dimension)
        if limit <= 0:
            return []
        try:
            with self._session_factory() as db:
                rows = (
                    db.execute(
                        text("""SELECT a.id AS article_id,r.version AS revision,r.title,k.content AS text
                    FROM knowledge_chunks k
                    JOIN articles a ON a.tenant_id=k.tenant_id AND a.id=k.article_id
                    JOIN article_revisions r ON r.tenant_id=a.tenant_id AND r.article_id=a.id
                      AND r.id=k.revision_id AND r.id=a.current_revision_id
                    JOIN widget_sections ws ON ws.tenant_id=a.tenant_id AND ws.section_id=a.section_id
                    JOIN widgets w ON w.tenant_id=ws.tenant_id AND w.id=ws.widget_id
                    WHERE k.tenant_id=:tenant AND ws.widget_id=:widget AND w.enabled=true
                      AND a.visibility='public' AND a.status='published'
                      AND k.embedding_model=:model AND k.embedding_dimension=:dimension
                    ORDER BY k.embedding <=> CAST(:query AS vector), a.id, k.position LIMIT :limit"""),
                        {
                            "tenant": tenant_id,
                            "widget": widget_id,
                            "model": model,
                            "dimension": dimension,
                            "query": encoded,
                            "limit": min(limit, 5),
                        },
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError:
            raise KnowledgeSearchFailure("search_unavailable") from None
        return [KnowledgeSnippet(**dict(row), visibility="public") for row in rows]
